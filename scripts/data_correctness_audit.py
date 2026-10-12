"""Stage D+ before/after audit (read-only).

Usage: uv run python scripts/data_correctness_audit.py [--before data/stocks.pre-stage-d-plus.db]

"Before" numbers come from the pre-repair backup (legacy labels, legacy universe
rule, raw close). "After" uses the live DB, the classifier-based universe and the
panel's adjusted-price factor series.
"""
import argparse
import bisect
import datetime as dt
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

START, END = '2021-09-18', '2026-09-18'
FIRST_SIGNAL = '2022-10-31'
LIMIT = 0.11  # TW daily limit is 10%: beyond this a 1-day move is not a market move


def ro(path):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def legacy_universe(c, as_of):
    y = dt.date.fromisoformat(as_of)
    start = y.replace(year=y.year - 1, day=min(y.day, 28)).isoformat()
    return [r[0] for r in c.execute(
        '''SELECT q.stock_id FROM daily_quotes q JOIN stocks s ON s.stock_id=q.stock_id
           WHERE q.date>=? AND q.date<? AND q.amount>=0 AND s.type='stock'
             AND (s.listed_date IS NULL OR s.listed_date<?) AND (s.delisted_date IS NULL OR s.delisted_date>?)
           GROUP BY q.stock_id HAVING AVG(q.amount)>=30000000 AND COUNT(*)>=120''',
        (start, as_of, as_of, as_of))]


def actions_section(before, after):
    for label, c in (('BEFORE (backup)', before), ('AFTER (live)', after)):
        print(f'\n### corporate_actions {label}')
        print('  kind:', dict(c.execute('SELECT kind,COUNT(*) FROM corporate_actions GROUP BY 1 ORDER BY 2 DESC').fetchall()))
        cols = {r[1] for r in c.execute('PRAGMA table_info(corporate_actions)')}
        if 'resolution' in cols:
            print('  resolution:', dict(c.execute(
                'SELECT COALESCE(resolution,"(none)"),COUNT(*) FROM corporate_actions GROUP BY 1 ORDER BY 2 DESC').fetchall()))
            print('  cash_dividend filled:', c.execute('SELECT COUNT(*) FROM corporate_actions WHERE cash_dividend IS NOT NULL').fetchone()[0],
                  ' share_multiplier filled:', c.execute('SELECT COUNT(*) FROM corporate_actions WHERE share_multiplier IS NOT NULL').fetchone()[0])
        else:
            print('  cash_dividend filled:', c.execute('SELECT COUNT(*) FROM corporate_actions WHERE cash_dividend IS NOT NULL').fetchone()[0],
                  ' share_multiplier: (column absent)')


def universe_section(before, after, sa):
    from app.services import data_foundation as data
    from app.security_class import classify_security
    months = ['2022-11-01', '2023-05-01', '2024-05-01', '2025-05-01', '2026-01-01', '2026-06-01', '2026-09-01']
    print('\n### universe size: legacy rule (type=stock) vs classifier')
    for m in months:
        old = legacy_universe(before, m)
        new = data.liquid_universe(sa, m)
        info = {r['stock_id']: r['class_basis'] for r in new}
        removed = Counter(classify_security(s, *(after.execute('SELECT name,industry,type FROM stocks WHERE stock_id=?', (s,)).fetchone() or (None, None, None)))[0]
                          for s in set(old) - set(info))
        print(f'  {m}: legacy {len(old)} -> new {len(new)}  removed {dict(removed)}  fallback-basis {sum(1 for b in info.values() if b != "industry")}')
    new = data.liquid_universe(sa, '2026-09-18')
    print('  @2026-09-18 classes kept:', Counter(r['security_class'] for r in new),
          'basis:', dict(Counter(r['class_basis'] for r in new)))
    left = [r['stock_id'] for r in new if r['stock_id'].startswith('00') or r['stock_id'].startswith('91')
            or not r['stock_id'].isdigit()]
    print('  ETF/DR/preferred-looking ids still in universe:', left)


def jump_section(after, sa):
    from app.services import data_foundation as data
    from app.services.strategy.panel import build_panel
    months = []
    for y in range(2022, 2027):
        for m in range(1, 13):
            d = f'{y}-{m:02d}-01'
            if '2022-11-01' <= d <= '2026-09-01':
                months.append(d)
    universes = {m: {r['stock_id'] for r in data.liquid_universe(sa, m)} for m in months}
    ids = sorted(set().union(*universes.values()))
    panel = build_panel(START, END, ids, benchmark_id=None)
    dates = [d.date for d in panel.days if d.is_trading_session]
    print(f'\n### jump audit on {len(ids)} names (union of monthly universes), {len(dates)} sessions')
    print('  panel warnings:', list(panel.warnings))
    raw_hits, adj_hits = {}, {}
    prev_raw, prev_adj, prev_idx, adj_gap = {}, {}, {}, {}
    session_no = {d: i for i, d in enumerate(d.date for d in panel.days if d.is_trading_session)}
    for day in panel.days:
        for sid, bar in day.bars.items():
            if bar.close and bar.close > 0:
                a = bar.adj_close if bar.adj_close is not None else bar.close
                if sid in prev_raw:
                    if abs(bar.close / prev_raw[sid] - 1) > LIMIT:
                        raw_hits.setdefault(sid, []).append(day.date)
                    if abs(a / prev_adj[sid] - 1) > LIMIT:
                        adj_hits.setdefault(sid, []).append(day.date)
                        adj_gap[(sid, day.date)] = session_no.get(day.date, 0) - prev_idx[sid]
                prev_raw[sid], prev_adj[sid], prev_idx[sid] = bar.close, a, session_no.get(day.date, 0)
    n_raw = sum(len(v) for v in raw_hits.values())
    n_adj = sum(len(v) for v in adj_hits.values())
    print(f'  >11% one-day moves: raw {n_raw} events / {len(raw_hits)} names  ->  adjusted {n_adj} events / {len(adj_hits)} names')
    cal = dates

    def share(hits, lookback):
        out = []
        for m in months:
            d = bisect.bisect_left(cal, m) - 1
            if d < lookback or cal[d] < FIRST_SIGNAL:
                continue
            univ = universes[m]
            lo, hi = cal[d - lookback], cal[d]
            hit = {s for s, v in hits.items() if any(lo <= x <= hi for x in v)}
            out.append(len(hit & univ) / max(1, len(univ)))
        return out
    for name, lb in (('momentum window (273d)', 273), ('volatility window (126d)', 126)):
        r, a = share(raw_hits, lb), share(adj_hits, lb)
        print(f'  {name}: share of universe hit  raw mean {statistics.mean(r):.3f} max {max(r):.3f}'
              f'  ->  adjusted mean {statistics.mean(a):.3f} max {max(a):.3f}')
    remaining = sorted(((s, d) for s, v in adj_hits.items() for d in v), key=lambda x: x[1])
    print('  remaining adjusted events (first 25):', remaining[:25])
    # explained vs unexplained remainder: event within +-3d of an action row?
    acts = {}
    for r in after.execute('SELECT stock_id,date,resolution FROM corporate_actions'):
        acts.setdefault(r[0], []).append((r[1], r[2]))
    near = 0
    for s, d in remaining:
        if any(abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(x)).days) <= 3 for x, _ in acts.get(s, [])):
            near += 1
    late = [(k, g) for k, g in adj_gap.items() if k[1] >= FIRST_SIGNAL]
    print(f'  remaining events on/after {FIRST_SIGNAL}: {len(late)}; of which after a >5-session quote gap '
          f'(suspension/resume): {sum(1 for _, g in late if g > 5)}; '
          f'before-signal-start (IPO-era 2021-22): {len(adj_gap) - len(late)}')
    print('  post-signal remaining (first 15):', sorted(late)[:15])
    print(f'  remaining events with an action row within 3d: {near}; without: {len(remaining) - near}')
    return panel


def unresolved_section(after, panel):
    print('\n### unresolved corporate actions in universe (any date in window)')
    ids = set(panel.securities)
    rows = after.execute(
        "SELECT stock_id,date,resolution FROM corporate_actions WHERE date>=? AND date<=?",
        ('2022-10-31', END)).fetchall()
    c = Counter(r[2] for r in rows if r[0] in ids)
    print('  by resolution (2022-10-31+):', dict(c))
    unresolved = sum(v for k, v in c.items() if k and k.startswith('unresolved'))
    print(f'  unresolved {unresolved} of {sum(c.values())} events ({unresolved / max(1, sum(c.values())):.1%})')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--before', default=str(ROOT / 'data' / 'stocks.pre-stage-d-plus.db'))
    ap.add_argument('--after', default=str(ROOT / 'data' / 'stocks.db'))
    a = ap.parse_args()
    from app.db import engine
    before, after = ro(a.before), ro(a.after)
    actions_section(before, after)
    with engine.connect() as sa:  # data_foundation helpers take a SQLAlchemy connection (live DB)
        universe_section(before, after, sa)
        p = jump_section(after, sa)
    unresolved_section(after, p)
