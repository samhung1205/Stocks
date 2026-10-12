"""Stage A+ data access/ingestion, deliberately no backtest engine or panel."""
import datetime as dt
import math

from app import config
from app.db import query_all, upsert_many
from app.security_class import COMMON_STOCK, classify_security
from app.db import tables as t
from app.datasources import finmind
from app.datasources.base import get_json, roc_to_iso


def year_before(value: str, years: int = 1) -> str:
    d = dt.date.fromisoformat(value)
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:
        return d.replace(year=d.year - years, day=28).isoformat()


def available_fundamentals(conn, table: str, as_of: str, *, allow_assumed=False):
    """Date-only evidence: available from following calendar day.

    Strict excludes unknown/assumed publication. Observed FinMind dates prove
    provider observation, not issuer announcement or historical revision vintage.
    Assumed mode is explicitly opt-in and cannot guarantee no look-ahead.
    """
    if table not in ('month_revenue', 'financials'):
        raise ValueError('Unsupported publication table')
    as_of = dt.date.fromisoformat(as_of).isoformat()
    sources = "('actual','observed_finmind','assumed_deadline')" if allow_assumed else "('actual','observed_finmind')"
    return query_all(conn, f'SELECT * FROM {table} WHERE publish_date < :d '
                     f'AND publish_source IN {sources}', d=as_of)


def liquid_universe(conn, as_of: str, min_avg_amount: float = config.BACKFILL_MIN_AVG_AMOUNT,
                    min_observations: int = 120) -> list[dict]:
    """Trailing calendar year, strictly before as_of; never a fixed stock count.

    At least min_observations quotes with valid turnover. Unknown listing dates
    remain visible in metadata; historical quotes establish observed existence.
    Delisting day is excluded. No future amount or current survivor-only list.
    """
    as_of = dt.date.fromisoformat(as_of).isoformat()
    if not math.isfinite(min_avg_amount) or min_avg_amount < 0 or min_observations < 1:
        raise ValueError('Invalid liquidity parameters')
    rows = query_all(conn, '''SELECT q.stock_id, s.name, s.listed_date, s.delisted_date,
        s.industry, s.type AS stocks_type,
        AVG(q.amount) avg_daily_amount, COUNT(*) observations,
        MIN(q.date) first_observation, MAX(q.date) last_observation
        FROM daily_quotes q JOIN stocks s ON s.stock_id=q.stock_id
        WHERE q.date >= :start AND q.date < :end AND q.amount >= 0
          AND (s.listed_date IS NULL OR s.listed_date < :end)
          AND (s.delisted_date IS NULL OR s.delisted_date > :end)
        GROUP BY q.stock_id HAVING AVG(q.amount) >= :threshold AND COUNT(*) >= :minimum
        ORDER BY avg_daily_amount DESC, q.stock_id''',
        start=year_before(as_of), end=as_of, threshold=min_avg_amount, minimum=min_observations)
    # Only ordinary shares: ETF/ETN/REIT/DR/preferred/warrants are excluded by the
    # explicit classifier (app/security_class.py), not by stocks.type or a '00%' guess.
    out = []
    for r in rows:
        cls, basis = classify_security(r['stock_id'], r['name'], r['industry'], r.pop('stocks_type'))
        if cls == COMMON_STOCK:
            r.pop('industry')
            out.append({**r, 'security_class': cls, 'class_basis': basis})
    return out


def ingest_stock_info(conn, rows):
    out = {}
    for r in rows:
        sid, market = str(r.get('stock_id', '')), r.get('type')
        if market not in ('twse', 'tpex') or not (4 <= len(sid) <= 6) or not sid[:4].isdigit():
            continue
        cat = r.get('industry_category') or ''
        # Explicitly exclude warrants/ETNs and other instruments from stock universe.
        kind = 'etf' if cat == 'ETF' else ('stock' if len(sid) == 4 and sid.isdigit() and not sid.startswith('0') else 'other')
        if kind == 'other':
            continue
        out.setdefault(sid, {'stock_id': sid, 'name': r.get('stock_name') or sid,
                            'market': market, 'type': kind, 'industry': cat})
    # TaiwanStockInfoWithWarrant.date is UPDATED date, never a listing date.
    return upsert_many(conn, t.stocks, list(out.values()))


def official_listings(market):
    url, field = {
        'twse': ('https://openapi.twse.com.tw/v1/opendata/t187ap03_L', '上市日期'),
        'tpex': ('https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O', 'DateOfListing'),
    }[market]
    rows = get_json(url)
    out = []
    for r in rows:
        value = str(r.get(field) or '').replace('/', '').replace('-', '')
        if len(value) == 8 and value.isdigit():
            date = f'{value[:4]}-{value[4:6]}-{value[6:]}'
        else:
            date = roc_to_iso(value)
        sid = str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or '')
        if sid and date:
            dt.date.fromisoformat(date)
            out.append({'stock_id': sid, 'name': r.get('公司簡稱') or r.get('公司名稱') or r.get('CompanyAbbreviation') or sid,
                        'market': market, 'type': 'stock', 'listed_date': date})
    if not out:
        raise ValueError(f'{market}: missing official listing dates')
    return out


def financial_history(stock_id, start, end):
    from app.services.deep_fetch import (INCOME_MAP, BALANCE_MAP, CASHFLOW_MAP,
                                        _pivot_statement, _decumulate_cashflow)
    acc = {}
    # Fetch from start of year so cash-flow Qn always has preceding cumulative Q.
    start = start[:4] + '-01-01'
    for dataset, mapping in [('TaiwanStockFinancialStatements', INCOME_MAP),
                             ('TaiwanStockBalanceSheet', BALANCE_MAP),
                             ('TaiwanStockCashFlowsStatement', CASHFLOW_MAP)]:
        rows = finmind.fetch(dataset, stock_id, start, end)
        _pivot_statement([r for r in rows if start <= r['date'] <= end], mapping, acc)
    _decumulate_cashflow(acc)
    return [{'stock_id': stock_id, 'quarter': q, **values} for q, values in sorted(acc.items())]


def coverage(conn):
    result = {}
    for name, col in [('daily_quotes', 'date'), ('valuations', 'date'),
                      ('benchmark_series', 'date'), ('corporate_actions', 'date'),
                      ('month_revenue', 'ym'), ('financials', 'quarter')]:
        result[name] = query_all(conn, f'SELECT COUNT(*) rows, COUNT(DISTINCT {col}) periods, '
                                 f'MIN({col}) first, MAX({col}) last FROM {name}')[0]
    result['publication'] = {table: query_all(conn, f'SELECT publish_source,COUNT(*) rows FROM {table} '
                                             'GROUP BY publish_source') for table in ('month_revenue', 'financials')}
    result['listings'] = query_all(conn, 'SELECT COUNT(*) stocks,COUNT(listed_date) listed_known,'
                                  'COUNT(delisted_date) delisted_known FROM stocks')[0]
    result['full_market_days'] = query_all(conn, 'SELECT COUNT(*) days,MIN(date) first,MAX(date) last FROM '
                                          '(SELECT date FROM daily_quotes GROUP BY date HAVING COUNT(*)>1000)')[0]
    return result
