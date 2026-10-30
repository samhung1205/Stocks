"""Local-only light-dataset evidence. No network and no writes.

Per-market row floors are disclosure thresholds, not a claim that every listed
security traded. A single deep-fetch row must never certify a whole market.
The bounded session window comes from trading_calendar; absent calendar evidence
is reported unverified. TAIEX_TR is deliberately outside this light-layer check.
"""
import datetime as dt

from app.db import query_all, query_one

MIN_MARKET_ROWS = {'twse': 500, 'tpex': 400}
LOOKBACK_DAYS = 10


def _counts(conn, table, start, target, valid):
    rows = query_all(conn, f'''SELECT d.date,s.market,COUNT(*) n FROM {table} d
        JOIN stocks s ON s.stock_id=d.stock_id
        WHERE d.date>=:a AND d.date<=:b AND s.market IN ('twse','tpex')
          AND ({valid}) GROUP BY d.date,s.market''', a=start, b=target)
    result = {}
    for row in rows:
        result.setdefault(row['date'], {})[row['market']] = row['n']
    return result


def check(conn, target):
    if not target:
        return {}
    start = (dt.date.fromisoformat(target) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    sessions = [r['date'] for r in query_all(conn,
        'SELECT date FROM trading_calendar WHERE date>=:a AND date<=:b ORDER BY date', a=start, b=target)]
    verified = bool(sessions and target in sessions)
    # Always inspect the officially resolved target even if the imported calendar
    # omitted it; never use the calendar to dismiss official current-day evidence.
    expected = sorted(set(sessions + [target]))
    markets = {r['market'] for r in query_all(conn, 'SELECT DISTINCT market FROM stocks')}
    metadata_ready = set(MIN_MARKET_ROWS) <= markets
    result = {}
    for key, table, valid in (
        ('quotes', 'daily_quotes', 'd.close IS NOT NULL'),
        ('institutional', 'institutional_daily', 'd.total_net IS NOT NULL'),
        ('valuations', 'valuations', 'd.pe IS NOT NULL OR d.pb IS NOT NULL OR d.dividend_yield IS NOT NULL'),
    ):
        counts = _counts(conn, table, start, target, valid)
        latest = query_one(conn, f'SELECT MAX(date) d FROM {table} WHERE date<=:t', t=target)['d']
        missing = [date for date in expected if any(
            counts.get(date, {}).get(market, 0) < floor for market, floor in MIN_MARKET_ROWS.items())]
        state = 'missing' if missing else 'current'
        scope = 'bounded_trading_sessions'
        if key == 'valuations':
            # Official latest valuation endpoints commonly publish one session
            # behind quotes. Keep that date visible; do not relabel it as today.
            complete = [date for date, values in counts.items() if all(
                values.get(market, 0) >= floor for market, floor in MIN_MARKET_ROWS.items())]
            last_complete = max(complete) if complete else None
            previous = max((date for date in sessions if date < target), default=None)
            state = ('current' if last_complete == target else
                     'publication_lag' if previous and last_complete == previous and latest == previous else 'missing')
            missing = [] if state in ('current', 'publication_lag') else [target]
            scope = 'latest_published_session_only'
        if not metadata_ready or not verified:
            state = 'unverified' if state in ('current', 'publication_lag') else state
        result[key] = {'state': state, 'latest_date': latest, 'missing_dates': missing,
                       'market_counts': counts.get(target, {}), 'minimum_by_market': dict(MIN_MARKET_ROWS),
                       'calendar_verified': verified, 'scope': scope,
                       'checked_from': start, 'checked_through': target}
    have = {r['date'] for r in query_all(conn, '''SELECT date FROM market_summary
        WHERE date>=:a AND date<=:b AND taiex_close IS NOT NULL AND total_amount IS NOT NULL''', a=start, b=target)}
    missing = [date for date in expected if date not in have]
    result['market_summary'] = {
        'state': 'missing' if missing else 'current' if verified else 'unverified',
        'latest_date': query_one(conn, 'SELECT MAX(date) d FROM market_summary WHERE date<=:t', t=target)['d'],
        'missing_dates': missing, 'calendar_verified': verified,
        'scope': 'bounded_trading_sessions', 'checked_from': start, 'checked_through': target}
    return result


def complete(datasets):
    return bool(datasets) and all(row['state'] in ('current', 'publication_lag') for row in datasets.values())


def missing_dates(datasets):
    return sorted({date for row in datasets.values() for date in row['missing_dates']})
