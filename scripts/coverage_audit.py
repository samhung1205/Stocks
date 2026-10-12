"""Strategy Lab Coverage Gate — read-only audit of data/stocks.db.

Usage: uv run python scripts/coverage_audit.py [section ...]
Sections: job prices universe fundamentals actions size (default: all)
Only SELECTs; changes no product logic or data.
"""
import datetime as dt
import sqlite3
import statistics
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / 'data' / 'stocks.db'
JOB = 'e743c5a372cc487287fa4054290640fd'
START, END = '2021-09-18', '2026-09-18'
MIN_AMT, MIN_OBS = 30_000_000, 120


def conn():
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def q(c, sql, *a):
    return [dict(r) for r in c.execute(sql, a)]


def show(title, rows, limit=40):
    print(f'\n### {title}')
    for r in rows[:limit]:
        print('  ', r)
    if len(rows) > limit:
        print(f'   ... {len(rows) - limit} more')


def job(c):
    show('task status', q(c, 'select dataset,status,count(*) n,sum(rows_written) rows from backfill_tasks '
                             'where job_id=? group by 1,2', JOB))
    show('pending/failed/error/running', q(c, "select status,count(*) n from backfill_tasks where job_id=? "
                                             "and status not in ('done','empty','skipped_closed') group by 1", JOB))
    show('job row', q(c, 'select id,status from backfill_jobs'))
    emp = q(c, '''select t.target, s.name, s.type, s.market, group_concat(t.dataset) ds
                  from backfill_tasks t left join stocks s on s.stock_id=t.target
                  where t.job_id=? and t.status='empty' group by 1''', JOB)
    for r in emp:
        t = r['target']
        r['class'] = ('bond_etf' if t.startswith('00') and t[-1] in 'BD' else
                      'etf' if t.startswith('00') else
                      'DR' if t.startswith('91') else 'preferred/other')
    cnt = {}
    for r in emp:
        cnt[r['class']] = cnt.get(r['class'], 0) + 1
    print('\n### empty targets by class', cnt, 'total', len(emp))
    show('non-ETF empty', [r for r in emp if r['class'] not in ('bond_etf', 'etf')])


def prices(c):
    cal = {r['date'] for r in q(c, 'select date from trading_calendar where date between ? and ?', START, END)}
    rows = q(c, '''select d.date, sum(s.market='twse') tw, sum(s.market='tpex') tp, count(*) n
                   from daily_quotes d left join stocks s using(stock_id)
                   where d.date between ? and ? group by 1 order by 1''', START, END)
    have = {r['date'] for r in rows}
    print('\n### calendar days', len(cal), 'quote days', len(have))
    print('   in calendar not quotes:', sorted(cal - have))
    print('   in quotes not calendar:', sorted(have - cal))
    ns = [r['n'] for r in rows]
    med = statistics.median(ns)
    print('   n rows/day min/median/max', min(ns), med, max(ns))
    print('   twse min/med/max', min(r['tw'] for r in rows), statistics.median(r['tw'] for r in rows), max(r['tw'] for r in rows))
    print('   tpex min/med/max', min(r['tp'] for r in rows), statistics.median(r['tp'] for r in rows), max(r['tp'] for r in rows))
    show('lowest-count days', sorted(rows, key=lambda r: r['n'])[:10])
    show('days < 85% of median', [r for r in rows if r['n'] < 0.85 * med])
    show('by year avg n', q(c, '''select substr(date,1,4) y, count(*) days, round(avg(n)) avg_n, min(n) min_n from
        (select date,count(*) n from daily_quotes where date between ? and ? group by 1) group by 1''', START, END))
    show('dups', q(c, 'select count(*) n from (select 1 from daily_quotes group by stock_id,date having count(*)>1)'))
    show('bad rows', q(c, '''select sum(close is null or close<=0) bad_close,
        sum(high<low) hi_lt_lo, sum(volume=0) zero_vol, sum(amount is null) null_amt from daily_quotes
        where date between ? and ?''', START, END))
    show('pre-start quote days', q(c, '''select count(distinct date) days,
        min(date) mn, max(date) mx, min(n) min_n, max(n) max_n from (select date,count(*) n from daily_quotes
        where date<? group by 1)''', START))
    show('benchmark', q(c, 'select benchmark_id, count(*) n, min(date) mn, max(date) mx from benchmark_series group by 1'))
    bd = {r['date'] for r in q(c, "select date from benchmark_series where benchmark_id='TAIEX_TR' "
                                    "and date between ? and ?", START, END)}
    print('   TAIEX_TR expected(cal∩quotes) vs actual:', len(cal & have), len(bd))
    print('   missing:', sorted((cal & have) - bd), ' extra:', sorted(bd - (cal & have)))
    show('2026-07-10', q(c, "select 'calendar' k,count(*) n from trading_calendar where date='2026-07-10' union all "
                            "select 'quotes',count(*) from daily_quotes where date='2026-07-10' union all "
                            "select 'benchmark',count(*) from benchmark_series where date='2026-07-10' union all "
                            "select 'valuations',count(*) from valuations where date='2026-07-10'"))
    vals = q(c, 'select value,date from benchmark_series where benchmark_id=? and date between ? and ? order by date',
             'TAIEX_TR', START, END)
    big = [(a['date'], b['date'], round(b['value'] / a['value'] - 1, 4)) for a, b in zip(vals, vals[1:])
           if abs(b['value'] / a['value'] - 1) > 0.07]
    print('   TAIEX_TR |daily move|>7%:', big)


def universe(c):
    firsts = ['2021-09-18']
    for y in range(2021, 2027):
        for m in range(1, 13):
            d = f'{y}-{m:02d}-01'
            if '2021-10-01' <= d <= '2026-09-01':
                firsts.append(d)
    sizes = {}
    sys.path.insert(0, str(DB.parents[1]))
    from app.db import engine
    from app.services import data_foundation
    with engine.connect() as sa:  # Stage D+: classifier-based universe (ordinary shares only)
        for d in firsts:
            rows = data_foundation.liquid_universe(sa, d, MIN_AMT, MIN_OBS)
            sizes[d] = (len(rows), sum(1 for r in rows if r['class_basis'] != 'industry'))
    print('\n### monthly universe (as_of: ordinary shares, of which id-pattern-fallback classified)')
    for d, v in sizes.items():
        print('  ', d, v)
    for lbl, lo in (('all', '0'), ('from 2022-10-01', '2022-10-01')):
        ns = [v[0] for k, v in sizes.items() if k >= lo]
        print(f'   min/median/max ({lbl}):', min(ns), statistics.median(ns), max(ns))
    rows = []
    for s in ('2021-09-18', '2022-03-01', '2022-08-02', '2022-09-19', '2022-10-03', '2022-11-01'):
        d = dt.date.fromisoformat(s)
        rows.append({'start': s, 'prior_12m_full_market_days': q(c, '''select count(*) n from (select date from daily_quotes
            where date>=? and date<? group by 1 having count(*)>1000)''', d.replace(year=d.year - 1).isoformat(), s)[0]['n']})
    show('full-market quote days in 12m before candidate start', rows)
    show('LEGACY rule only (type=stock): ETF-coded names; classifier universe above excludes them', q(c, '''select d.stock_id, s.name from daily_quotes d
        join stocks s using(stock_id) where d.date>=? and d.date<? and s.type='stock' and d.stock_id like '00%'
        group by 1 having avg(d.amount)>=? and count(*)>=?''', '2025-09-18', '2026-09-18', MIN_AMT, MIN_OBS), 80)
    show('stocks types', q(c, "select type, market, sum(stock_id like '00%') etf_coded, count(*) n from stocks group by 1,2"))


def fundamentals(c):
    for tbl, per, lo in (('month_revenue', 'ym', '2021-01'), ('financials', 'quarter', '2021Q1')):
        show(f'{tbl} source mix', q(c, f'select publish_source, count(*) n from {tbl} group by 1'))
        show(f'{tbl} per {per}', q(c, f'''select {per} p, count(*) n,
            sum(publish_source='assumed_deadline') assumed, sum(publish_source='observed_finmind') observed,
            sum(publish_source='actual') actual from {tbl} where {per}>=? group by 1 order by 1''', lo), 80)
        show(f'{tbl} stock coverage', q(c, f'select count(distinct stock_id) stocks, min({per}) mn, max({per}) mx from {tbl}'))
    show('month_revenue null publish_date', q(c, 'select count(*) n from month_revenue where publish_date is null'))
    show('financials null rates 2021Q3+', q(c, '''select count(*) n, sum(revenue is null) rev_null,
        sum(net_income is null) ni_null, sum(equity is null) eq_null, sum(total_assets is null) ta_null,
        sum(gross_profit is null) gp_null, sum(op_cashflow is null) ocf_null, sum(eps is null) eps_null
        from financials where quarter>='2021Q3' '''))
    show('valuations per year', q(c, '''select substr(date,1,4) y, count(distinct date) days, count(*) n,
        count(distinct stock_id) stocks, round(avg(pe is null),3) pe_null, round(avg(pb is null),3) pb_null
        from valuations group by 1'''))
    show('valuation vs quote days', q(c, '''select (select count(distinct date) from valuations where date between ? and ?) val_days,
        (select count(distinct date) from daily_quotes where date between ? and ?) quote_days''', START, END, START, END))
    show('quote days without valuations', q(c, '''select date from (select distinct date from daily_quotes
        where date between ? and ?) where date not in (select distinct date from valuations) order by 1 limit 20''', START, END))
    show('valuation days with <1000 rows', q(c, 'select date, count(*) n from valuations group by 1 having n<1000 order by 1 limit 20'))
    for asof in ('2023-09-01', '2026-09-18'):
        st = dt.date.fromisoformat(asof).replace(year=int(asof[:4]) - 1).isoformat()
        u = [r['stock_id'] for r in q(c, '''select d.stock_id from daily_quotes d join stocks s using(stock_id)
            where d.date>=? and d.date<? and s.type='stock' and s.stock_id not like '00%' group by 1
            having avg(d.amount)>=? and count(*)>=?''', st, asof, MIN_AMT, MIN_OBS)]
        ph = ','.join('?' * len(u))
        r = q(c, f'''select
          (select count(distinct stock_id) from month_revenue where stock_id in ({ph}) and ym>='2022-01') rev_stocks,
          (select count(distinct stock_id) from financials where stock_id in ({ph}) and quarter>='2022Q1') fin_stocks,
          (select count(distinct stock_id) from valuations where stock_id in ({ph}) and date>=?) val_stocks''',
              *u, *u, *u, st)[0]
        print(f'\n### stock-only universe @ {asof}: {len(u)} names -> {r}')
    show('strict-usable revenue by publish month', q(c, '''select substr(publish_date,1,7) m, count(*) n
        from month_revenue where publish_source in ('actual','observed_finmind') group by 1 order by 1'''))
    show('strict-usable financials', q(c, "select count(*) n from financials where publish_source in ('actual','observed_finmind')"))
    show('revenue months per stock in 2024', q(c, '''select sum(n=12) full12, sum(n between 6 and 11) part, sum(n<6) sparse
        from (select stock_id,count(*) n from month_revenue where ym between '2024-01' and '2024-12' group by 1)'''))
    show('financial quarters per stock 2022Q1..2026Q2', q(c, '''select n, count(*) stocks from
        (select stock_id,count(*) n from financials where quarter between '2022Q1' and '2026Q2' group by 1) group by 1 order by 1 desc'''), 30)
    show('financial publish window by quarter', q(c, '''select quarter, min(publish_date) mn, max(publish_date) mx
        from financials where quarter>='2024Q1' group by 1 order by 1'''))


def actions(c):
    show('corporate_actions kind/source', q(c, 'select kind,source,count(*) n,sum(ratio is not null) w_ratio, '
                                               'sum(cash_dividend is not null) w_cash, sum(stock_dividend is not null) w_stock, '
                                               'min(date) mn, max(date) mx from corporate_actions group by 1,2'))
    show('actions per year', q(c, 'select substr(date,1,4) y, kind, count(*) n from corporate_actions group by 1,2 order by 1,2'))
    show('non-dividend rows', q(c, "select stock_id,date,kind,source,before_price,after_price,ratio,raw_json from corporate_actions "
                                    "where kind not like '%dividend%' order by date"), 30)
    show('dividends table', q(c, 'select count(*) n, count(distinct stock_id) stocks from dividends'))
    show('raw-close jumps >25%', q(c, '''
        with x as (select stock_id,date,close, lag(close) over (partition by stock_id order by date) p
                   from daily_quotes where date between ? and ?),
        j as (select stock_id,date,close/p-1 r from x where p>0 and abs(close/p-1)>0.25)
        select count(*) jumps, count(distinct stock_id) stocks,
          sum(exists(select 1 from corporate_actions a where a.stock_id=j.stock_id and abs(julianday(a.date)-julianday(j.date))<=3)) with_action,
          sum(exists(select 1 from corporate_actions a where a.stock_id=j.stock_id and abs(julianday(a.date)-julianday(j.date))<=3 and a.ratio is not null)) with_ratio
        from j''', START, END))
    show('big jumps, liquid non-ETF names (top 30)', q(c, '''
        with x as (select stock_id,date,close, lag(close) over (partition by stock_id order by date) p
                   from daily_quotes where date between ? and ?)
        select x.stock_id, s.name, date, round(close/p-1,3) r, (select group_concat(kind||':'||coalesce(ratio,'null'))
          from corporate_actions a where a.stock_id=x.stock_id and abs(julianday(a.date)-julianday(x.date))<=3) act
        from x join stocks s using(stock_id) where p>0 and abs(close/p-1)>0.25 and x.stock_id not like '00%' and s.type='stock'
        and (select avg(amount) from daily_quotes d where d.stock_id=x.stock_id and d.date between date(x.date,'-365 day') and x.date)>=?
        order by abs(close/p-1) desc limit 30''', START, END, MIN_AMT), 30)
    show('ETF jumps >25%', q(c, '''
        with x as (select stock_id,date,close, lag(close) over (partition by stock_id order by date) p
                   from daily_quotes where date between ? and ? and stock_id like '00%')
        select stock_id,date,round(close/p-1,3) r from x where p>0 and abs(close/p-1)>0.25 order by date''', START, END), 20)


def size(c):
    print('\n### db size bytes', DB.stat().st_size)
    for t in [r['name'] for r in q(c, "select name from sqlite_master where type='table' order by 1")]:
        print('  ', t, q(c, f'select count(*) n from {t}')[0]['n'])
    print(q(c, 'pragma freelist_count'))


if __name__ == '__main__':
    c = conn()
    for s in sys.argv[1:] or ['job', 'prices', 'universe', 'fundamentals', 'actions', 'size']:
        globals()[s](c)
