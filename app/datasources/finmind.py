"""FinMind v4 REST（不用 SDK，直接打 API 保持輕量）。

免費註冊金鑰後 600 次/小時；未帶金鑰亦可用但額度較低。
深度資料主力來源：歷史價量、月營收、三大財報、法人/融資券明細、股利。
"""
from app.datasources.base import get_json
from app.config import FINMIND_TOKEN

API = "https://api.finmindtrade.com/api/v4/data"


class FinMindError(RuntimeError):
    pass


def fetch(dataset: str, data_id: str = "", start_date: str = "",
          end_date: str = "") -> list[dict]:
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else None
    data = get_json(API, params=params, headers=headers)
    if data.get("status") != 200:
        raise FinMindError(f"FinMind {dataset} {data_id}: {data.get('msg')}")
    return data.get("data") or []


def stock_info() -> list[dict]:
    """全市場股票/ETF 清單（stock_id, stock_name, industry_category, type）。"""
    return fetch("TaiwanStockInfo")


def price_history(stock_id: str, start_date: str) -> list[dict]:
    rows = fetch("TaiwanStockPrice", stock_id, start_date)
    return [{
        "stock_id": r["stock_id"], "date": r["date"],
        "open": r.get("open"), "high": r.get("max"),
        "low": r.get("min"), "close": r.get("close"),
        "change": r.get("spread"),
        "volume": r.get("Trading_Volume"), "amount": r.get("Trading_money"),
        "transactions": r.get("Trading_turnover"),
    } for r in rows]


def month_revenue(stock_id: str, start_date: str, end_date: str = "") -> list[dict]:
    """月營收（原始值；YoY/MoM 由服務層計算）。"""
    rows = fetch("TaiwanStockMonthRevenue", stock_id, start_date, end_date)
    return [{
        "stock_id": r["stock_id"],
        "ym": f"{r['revenue_year']}-{int(r['revenue_month']):02d}",
        "revenue": r.get("revenue"),
        **revenue_publication(r),
    } for r in rows]


def financial_statements(stock_id: str, start_date: str) -> list[dict]:
    return fetch("TaiwanStockFinancialStatements", stock_id, start_date)


def balance_sheet(stock_id: str, start_date: str) -> list[dict]:
    return fetch("TaiwanStockBalanceSheet", stock_id, start_date)


def cash_flows(stock_id: str, start_date: str) -> list[dict]:
    return fetch("TaiwanStockCashFlowsStatement", stock_id, start_date)


def institutional(stock_id: str, start_date: str) -> list[dict]:
    """個股法人買賣超歷史（buy/sell 股數，name 分法人別）。"""
    return fetch("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start_date)


def margin_short(stock_id: str, start_date: str) -> list[dict]:
    rows = fetch("TaiwanStockMarginPurchaseShortSale", stock_id, start_date)
    return [{
        "stock_id": r["stock_id"], "date": r["date"],
        "margin_balance": r.get("MarginPurchaseTodayBalance"),
        "short_balance": r.get("ShortSaleTodayBalance"),
    } for r in rows]


def dividend_results(stock_id: str, start_date: str) -> list[dict]:
    rows = fetch("TaiwanStockDividendResult", stock_id, start_date)
    return [{
        "stock_id": r["stock_id"], "date": r["date"],
        "dividend": r.get("stock_and_cache_dividend"),
    } for r in rows]


def revenue_publication(row: dict) -> dict:
    import datetime as dt
    value = str(row.get('create_time') or '')[:10]
    try:
        observed = dt.date.fromisoformat(value)
        y, m = int(row['revenue_year']), int(row['revenue_month'])
        next_month = dt.date(y + (m == 12), m % 12 + 1, 1)
        if observed >= next_month and value >= '2026-04-21':
            return {'publish_date': value, 'publish_source': 'observed_finmind'}
    except ValueError:
        pass
    return {}  # generic writer adds explicitly Assumed deadline


def valuation_history(stock_id: str, start: str, end: str) -> list[dict]:
    from app.datasources.base import num
    return [{'stock_id': r['stock_id'], 'date': r['date'],
             'pe': num(r.get('PER')), 'pb': num(r.get('PBR')),
             'dividend_yield': num(r.get('dividend_yield'))}
            for r in fetch('TaiwanStockPER', stock_id, start, end)
            if start <= r['date'] <= end]


def total_return_index(start: str, end: str) -> list[dict]:
    from app.datasources.base import num
    out = []
    for r in fetch('TaiwanStockTotalReturnIndex', 'TAIEX', start, end):
        value = num(r.get('price'))
        if r['stock_id'] != 'TAIEX' or value is None or value <= 0:
            raise FinMindError('Invalid TAIEX total return row')
        if start <= r['date'] <= end:
            out.append({'benchmark_id': 'TAIEX_TR', 'date': r['date'], 'value': value,
                        'source': 'FinMind:TaiwanStockTotalReturnIndex'})
    return out


def action_history(dataset: str, stock_id: str, start: str, end: str) -> list[dict]:
    import json
    from app import corporate_actions
    from app.datasources.base import num
    if dataset not in ('TaiwanStockDividendResult', 'TaiwanStockSplitPrice'):
        raise ValueError('Unsupported corporate action dataset')
    out = []
    for r in fetch(dataset, stock_id, start, end):
        if not start <= r['date'] <= end:
            continue
        derived = corporate_actions.fields_for('FinMind:' + dataset, r)
        out.append({'stock_id': r['stock_id'], 'date': r['date'],
                    'before_price': num(r.get('before_price')), 'after_price': num(r.get('after_price')),
                    # cash_dividend / share_multiplier are set only when verifiably derivable
                    # from the source record; mixed 權息 is left unresolved.
                    **derived,
                    'source': 'FinMind:' + dataset, 'raw_json': json.dumps(r, ensure_ascii=False)})
    return out
