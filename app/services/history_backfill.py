"""Durable, bounded Stage A+ jobs. One local runner per DB, no task framework.

Every unit writes data and its checkpoint in ONE transaction. Network calls are
outside write transactions. A process crash releases flock; resume retries the
uncommitted unit. Empty trading-day responses are failures, never holidays.
"""
import datetime as dt
import fcntl
import hashlib
import json
import math
import uuid
from contextlib import contextmanager
from pathlib import Path

from app import config
from app.db import engine, query_all, query_one, upsert_many
from app.db import tables as t
from app.datasources import finmind, twse, tpex
from app.services import data_foundation as data


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


@contextmanager
def runner_lock():
    # Same canonical SQLite path across CLI and web process; kernel releases on crash.
    # Stage L1.1: this is the SAME lock file as app.services.data_freshness.refresh_lock()
    # -- both this 5-year historical backfill and the Stage L1 daily light-layer refresh
    # write overlapping tables (daily_quotes, valuations), so they share one DB-writer
    # coordination mechanism instead of two locks that are blind to each other.
    path = Path(engine.url.database).resolve() if engine.url.database != ':memory:' else Path(':memory:')
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:20]
    lockpath = config.DATA_DIR / f'db-writer-{digest}.lock'
    with lockpath.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another backfill runner is active for this database') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _task(job_id, phase, dataset, target='', start='', end=''):
    return dict(job_id=job_id, task_key=f'{dataset}:{target}:{start}:{end}',
                phase=phase, dataset=dataset, target=target, start_date=start, end_date=end,
                status='pending', rows_written=0, attempts=0, updated_at=now())


def create_job(start=None, end=None, min_avg_amount=config.BACKFILL_MIN_AVG_AMOUNT,
               min_observations=120, stock_ids=None):
    end = end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    start = start or data.year_before(end, 5)
    lo, hi = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    start, end = lo.isoformat(), hi.isoformat()
    if lo > hi or hi >= dt.date.today() or (hi - lo).days > 366 * 10:
        raise ValueError('Use a past date range of at most 10 years')
    if not math.isfinite(min_avg_amount) or min_avg_amount < 0 or min_observations < 1:
        raise ValueError('Invalid liquidity settings')
    if stock_ids is not None and (not stock_ids or len(stock_ids) > 5000 or
                                 any(not s.isalnum() or not 4 <= len(s) <= 6 for s in stock_ids)):
        raise ValueError('Invalid stock_ids')
    jid = uuid.uuid4().hex
    settings = dict(start=start, end=end, min_avg_amount=min_avg_amount,
                    min_observations=min_observations, stock_ids=stock_ids,
                    universe_mode='explicit_sample' if stock_ids else 'trailing_year_monthly_union')
    tasks = [_task(jid, 0, dataset) for dataset in ('calendar', 'stock_info', 'delistings')]
    tasks += [_task(jid, 0, 'listings', market) for market in ('twse', 'tpex')]
    # Chunks limit response size and expose meaningful resume/progress boundaries.
    for year in range(lo.year, hi.year + 1):
        a, b = max(start, f'{year}-01-01'), min(end, f'{year}-12-31')
        tasks.append(_task(jid, 1, 'benchmark', start=a, end=b))
    d = lo
    while d <= hi:
        for market in ('twse', 'tpex'):
            tasks.append(_task(jid, 2, 'quotes', market, d.isoformat(), d.isoformat()))
        d += dt.timedelta(days=1)
    tasks.append(_task(jid, 3, 'universe', start=start, end=end))
    with engine.begin() as conn:
        upsert_many(conn, t.backfill_jobs, [dict(id=jid, status='pending', config_json=json.dumps(settings),
                                               created_at=now(), updated_at=now(), stop_requested=0)])
        upsert_many(conn, t.backfill_tasks, tasks)
    return status(jid)


def status(job_id=None):
    with engine.connect() as conn:
        if job_id is None:
            return {'jobs': query_all(conn, 'SELECT id,status,created_at,updated_at,error FROM backfill_jobs '
                                           'ORDER BY created_at DESC LIMIT 20'), 'coverage': data.coverage(conn),
                    'default_min_avg_amount': config.BACKFILL_MIN_AVG_AMOUNT}
        job = query_one(conn, 'SELECT * FROM backfill_jobs WHERE id=:id', id=job_id)
        if not job:
            raise ValueError('Unknown backfill job')
        job['config'] = json.loads(job.pop('config_json'))
        job['progress'] = query_all(conn, 'SELECT status,COUNT(*) units,SUM(rows_written) rows_written '
                                    'FROM backfill_tasks WHERE job_id=:id GROUP BY status', id=job_id)
        job['datasets'] = query_all(conn, 'SELECT dataset,status,COUNT(*) units FROM backfill_tasks '
                                    'WHERE job_id=:id GROUP BY dataset,status', id=job_id)
        job['errors'] = query_all(conn, 'SELECT task_key,error FROM backfill_tasks WHERE job_id=:id '
                                  "AND status IN ('failed','partial') LIMIT 20", id=job_id)
        job['diagnostics'] = query_all(conn, 'SELECT task_key,status,error FROM backfill_tasks WHERE job_id=:id '
                                       'AND error IS NOT NULL LIMIT 20', id=job_id)
        job['empty_units'] = query_all(conn, 'SELECT task_key FROM backfill_tasks WHERE job_id=:id '
                                      "AND status='empty' LIMIT 20", id=job_id)
        if job['status'] == 'running':
            try:
                with runner_lock():
                    job['status'] = 'interrupted'
            except ValueError:
                pass
        return job



def prepare_resume(job_id):
    with runner_lock():
        status(job_id)
        with engine.begin() as conn:
            conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                         .values(stop_requested=0, status='queued', updated_at=now()))


def pause(job_id):
    with engine.begin() as conn:
        if not query_one(conn, 'SELECT id FROM backfill_jobs WHERE id=:id', id=job_id):
            raise ValueError('Unknown backfill job')
        conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                     .values(stop_requested=1, updated_at=now()))
    return {'id': job_id, 'stop_requested': True}


def _month_ranges(start, end):
    """Yield inclusive calendar-month ranges within an inclusive ISO range."""
    current = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    while current <= last:
        following = (current.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        chunk_end = min(last, following - dt.timedelta(days=1))
        yield current.isoformat(), chunk_end.isoformat()
        current = following


def _official_market_state(date):
    """Confirm whether both official Taiwan cash markets were closed on a date.

    A valid but unusually small response is deliberately inconclusive. This avoids
    turning a partial official response into a false holiday classification.
    """
    compact = date.replace('-', '')
    twse_count = len(twse.daily_all_by_date(compact))
    tpex_count = len(tpex.daily_all_by_date(compact))
    detail = {'date': date, 'twse_rows': twse_count, 'tpex_rows': tpex_count}
    if twse_count >= 500 or tpex_count >= 400:
        return 'open', detail
    if twse_count == 0 and tpex_count == 0:
        return 'closed', detail
    return 'inconclusive', detail


def _fetch_benchmark(start, end):
    """Fetch and reconcile TAIEX total return data without weakening completeness.

    Monthly requests avoid provider response truncation. Any missing dates are
    retried individually, then checked against both official exchanges. Only an
    isolated, officially closed date can correct the imported trading calendar.
    A confirmed-open missing date remains an explicit partial result.
    """
    by_date = {}
    for chunk_start, chunk_end in _month_ranges(start, end):
        for row in finmind.total_return_index(chunk_start, chunk_end):
            by_date[row['date']] = row

    with engine.connect() as conn:
        expected = {r['date'] for r in query_all(
            conn, 'SELECT date FROM trading_calendar WHERE date BETWEEN :a AND :b',
            a=start, b=end)}

    initial_actual = set(by_date)
    initial_missing = expected - initial_actual
    initial_extra = initial_actual - expected

    # A small retry isolates provider gaps from range-size and pagination issues.
    retried = set()
    for _ in range(2):
        missing = expected - set(by_date)
        if not missing:
            break
        for date in sorted(missing):
            retry_end = (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()
            retried.add(date)
            for row in finmind.total_return_index(date, retry_end):
                if start <= row['date'] <= end:
                    by_date[row['date']] = row

    remaining_missing = expected - set(by_date)
    extra = set(by_date) - expected
    false_calendar_dates = set()
    calendar_missing_open_dates = set()
    source_missing_open_dates = set()
    source_extra_closed_dates = set()
    official_checks = []

    actual_dates = set(by_date)
    for date in sorted(remaining_missing):
        market_state, detail = _official_market_state(date)
        official_checks.append(detail)
        # Require observations on both sides so a boundary error cannot be
        # mistaken for a closed market day.
        isolated = any(value < date for value in actual_dates) and any(value > date for value in actual_dates)
        if market_state == 'closed' and isolated:
            false_calendar_dates.add(date)
        elif market_state == 'open':
            source_missing_open_dates.add(date)
        else:
            raise ValueError(f'Cannot verify missing TAIEX total return date {date}: {detail}')

    for date in sorted(extra):
        market_state, detail = _official_market_state(date)
        official_checks.append(detail)
        if market_state == 'open':
            calendar_missing_open_dates.add(date)
        elif market_state == 'closed':
            source_extra_closed_dates.add(date)
            by_date.pop(date, None)
        else:
            raise ValueError(f'Cannot verify extra TAIEX total return date {date}: {detail}')

    reconciled_expected = (expected - false_calendar_dates) | calendar_missing_open_dates
    final_missing = reconciled_expected - set(by_date)
    final_extra = set(by_date) - reconciled_expected
    partial = bool(final_missing or final_extra or source_extra_closed_dates)

    anomalies = (initial_missing or initial_extra or retried or false_calendar_dates or
                 calendar_missing_open_dates or source_missing_open_dates or source_extra_closed_dates)
    metadata = {}
    if anomalies:
        metadata = {
            'expected_count_initial': len(expected),
            'source_count_initial': len(initial_actual),
            'source_count_after_retry': len(by_date),
            'initial_missing_dates': sorted(initial_missing),
            'initial_extra_dates': sorted(initial_extra),
            'retried_dates': sorted(retried),
            'calendar_false_positive_dates': sorted(false_calendar_dates),
            'calendar_missing_open_dates': sorted(calendar_missing_open_dates),
            'source_missing_open_dates': sorted(source_missing_open_dates),
            'source_extra_closed_dates': sorted(source_extra_closed_dates),
            'expected_count_final': len(reconciled_expected),
            'source_count_final': len(set(by_date)),
            'final_missing_dates': sorted(final_missing),
            'final_extra_dates': sorted(final_extra),
            'official_checks': official_checks,
        }
    return (t.benchmark_series, [by_date[date] for date in sorted(by_date)],
            'partial' if partial else 'done', metadata)


def _apply_fetch_metadata(conn, metadata):
    """Apply calendar reconciliation atomically with data and checkpoint writes."""
    for date in metadata.get('calendar_false_positive_dates', []):
        conn.execute(t.trading_calendar.delete().where(t.trading_calendar.c.date == date))
    upsert_many(conn, t.trading_calendar,
                [{'date': date} for date in metadata.get('calendar_missing_open_dates', [])])


def _fetch(unit, settings):
    dataset, target = unit['dataset'], unit['target']
    start, end = unit['start_date'], unit['end_date']
    if dataset == 'calendar':
        rows = finmind.fetch('TaiwanStockTradingDate')
        dates = sorted({dt.date.fromisoformat(r['date']).isoformat() for r in rows})
        if not dates or dates[0] > settings['start'] or dates[-1] < settings['end']:
            # End may be weekend: allow final seven calendar days, not stale years.
            if not dates or dates[0] > settings['start'] or dates[-1] < (dt.date.fromisoformat(settings['end']) - dt.timedelta(days=7)).isoformat():
                raise ValueError('Trading calendar does not cover requested range')
        return t.trading_calendar, [{'date': d} for d in dates], 'done'
    if dataset == 'stock_info':
        rows = finmind.fetch('TaiwanStockInfoWithWarrant')
        if not rows:
            raise ValueError('Empty stock metadata')
        return None, rows, 'done'
    if dataset == 'delistings':
        rows = finmind.fetch('TaiwanStockDelisting')
        if not rows:
            raise ValueError('Empty delistings metadata')
        return t.stocks, [{'stock_id': str(r['stock_id']), 'name': r.get('stock_name') or str(r['stock_id']),
                           'delisted_date': dt.date.fromisoformat(r['date']).isoformat(),
                           **({'type': 'stock'} if len(str(r['stock_id'])) == 4 and not str(r['stock_id']).startswith('0') else {})}
                          for r in rows if 4 <= len(str(r['stock_id'])) <= 6], 'done'
    if dataset == 'listings':
        return t.stocks, data.official_listings(target), 'done'
    if dataset == 'quotes':
        with engine.connect() as conn:
            trading = query_one(conn, 'SELECT date FROM trading_calendar WHERE date=:d', d=start)
            etfs = {r['stock_id'] for r in query_all(conn, "SELECT stock_id FROM stocks WHERE type='etf'")}
        if not trading:
            return t.daily_quotes, [], 'skipped_closed'
        provider = twse if target == 'twse' else tpex
        rows = provider.daily_all_by_date(start.replace('-', ''))
        rows = [r for r in rows if r['stock_id'] in etfs or (len(r['stock_id']) == 4 and r['stock_id'].isdigit() and not r['stock_id'].startswith('0'))]
        minimum = 500 if target == 'twse' else 400
        if len(rows) < minimum or any(r['date'] != start for r in rows):
            raise ValueError(f'{target} {start}: incomplete trading day ({len(rows)} rows)')
        return t.daily_quotes, rows, 'done'
    if dataset == 'benchmark':
        return _fetch_benchmark(start, end)
    if dataset == 'valuations':
        rows = finmind.valuation_history(target, start, end)
        with engine.connect() as conn:
            expected = {r['date'] for r in query_all(conn, 'SELECT date FROM daily_quotes WHERE stock_id=:s '
                         'AND date BETWEEN :a AND :b AND close>0 AND volume>0', s=target, a=start, b=end)}
        state = 'partial' if expected - {r['date'] for r in rows} else 'done'
        return t.valuations, rows, state
    if dataset in ('TaiwanStockDividendResult', 'TaiwanStockSplitPrice'):
        return t.corporate_actions, finmind.action_history(dataset, target, start, end), 'done'
    if dataset == 'revenue':
        from app.services.deep_fetch import _compute_revenue_growth
        return t.month_revenue, _compute_revenue_growth(finmind.month_revenue(target, data.year_before(start), end)), 'done'
    if dataset == 'financials':
        return t.financials, data.financial_history(target, start, end), 'done'
    raise ValueError(f'Unknown dataset {dataset}')


def _expand_universe(conn, unit, settings):
    ids = settings['stock_ids']
    if ids is None:
        ids = set()
        # Union of historical monthly snapshots, not only today's survivors.
        dates = [r['d'] for r in query_all(conn, "SELECT MAX(date) d FROM daily_quotes WHERE date BETWEEN :a AND :b GROUP BY substr(date,1,7)",
                                           a=settings['start'], b=settings['end'])]
        for value in dates:
            asof = (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()
            ids.update(r['stock_id'] for r in data.liquid_universe(conn, asof, settings['min_avg_amount'], settings['min_observations']))
    if not ids:
        raise ValueError('No eligible stocks: complete daily history or adjust liquidity/observation settings')
    tasks = []
    for sid in sorted(ids):
        for dataset in ('valuations', 'TaiwanStockDividendResult', 'TaiwanStockSplitPrice', 'revenue', 'financials'):
            tasks.append(_task(unit['job_id'], 4, dataset, sid, settings['start'], settings['end']))
    upsert_many(conn, t.backfill_tasks, tasks)
    return len(ids)


def run(job_id, max_units=100, retry_failed=True, reset_stop=True):
    """max_units=0 runs to completion; default/API are bounded. Ctrl-C is resumable."""
    if max_units < 0:
        raise ValueError('max_units must be nonnegative')
    with runner_lock():
        job = status(job_id)
        settings = job['config']
        with engine.begin() as conn:
            conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                         .values(status='running', stop_requested=0 if reset_stop else job['stop_requested'], error=None, updated_at=now()))
            if retry_failed:
                conn.exec_driver_sql("UPDATE backfill_tasks SET status='pending',error=NULL WHERE job_id=? AND status='failed'", (job_id,))
        processed = 0
        try:
            while not max_units or processed < max_units:
                with engine.connect() as conn:
                    job = query_one(conn, 'SELECT * FROM backfill_jobs WHERE id=:id', id=job_id)
                    unit = query_one(conn, "SELECT * FROM backfill_tasks WHERE job_id=:id AND status='pending' ORDER BY phase,task_key LIMIT 1", id=job_id)
                if job['stop_requested'] or not unit:
                    break
                try:
                    metadata = {}
                    if unit['dataset'] == 'universe':
                        table, rows, state = None, [], 'done'
                    else:
                        result = _fetch(unit, settings)
                        if len(result) == 4:
                            table, rows, state, metadata = result
                        else:
                            table, rows, state = result
                    with engine.begin() as conn:
                        if unit['dataset'] == 'universe':
                            count = _expand_universe(conn, unit, settings)
                        elif unit['dataset'] == 'stock_info':
                            count = data.ingest_stock_info(conn, rows)
                        else:
                            if unit['dataset'] == 'quotes':
                                known = {r['stock_id'] for r in query_all(conn, 'SELECT stock_id FROM stocks')}
                                # Preserve historical issues absent from today's master.
                                missing = {r['stock_id'] for r in rows} - known
                                upsert_many(conn, t.stocks, [dict(stock_id=s, name=s, market=unit['target'],
                                    type='stock' if len(s)==4 and s.isdigit() and not s.startswith('0') else 'other')
                                    for s in sorted(missing)])
                            count = upsert_many(conn, table, rows)
                        _apply_fetch_metadata(conn, metadata)
                        if unit['dataset'] in ('valuations', 'revenue', 'financials') and not rows:
                            state = 'empty'  # successful query ≠ known complete data
                        conn.execute(t.backfill_tasks.update().where(
                            (t.backfill_tasks.c.job_id == job_id) & (t.backfill_tasks.c.task_key == unit['task_key']))
                            .values(status=state, rows_written=count, attempts=unit['attempts'] + 1,
                                    error=json.dumps(metadata, ensure_ascii=False) if metadata else None,
                                    updated_at=now()))
                        conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id).values(updated_at=now()))
                    processed += 1
                except Exception as exc:
                    # Stop early on quota/network/schema failures; retry on explicit resume.
                    message = f'{type(exc).__name__}: {exc}'[:1000]
                    with engine.begin() as conn:
                        conn.execute(t.backfill_tasks.update().where(
                            (t.backfill_tasks.c.job_id == job_id) & (t.backfill_tasks.c.task_key == unit['task_key']))
                            .values(status='failed', attempts=unit['attempts'] + 1, error=message, updated_at=now()))
                        conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                                     .values(status='failed', error=message, updated_at=now()))
                    return status(job_id)
        except BaseException:
            with engine.begin() as conn:
                conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                             .values(status='interrupted', updated_at=now()))
            raise
        with engine.begin() as conn:
            states = {r['status'] for r in query_all(conn, 'SELECT DISTINCT status FROM backfill_tasks WHERE job_id=:id', id=job_id)}
            final = 'failed' if 'failed' in states else ('paused' if 'pending' in states else ('completed_with_gaps' if states & {'empty', 'partial'} else 'completed'))
            conn.execute(t.backfill_jobs.update().where(t.backfill_jobs.c.id == job_id)
                         .values(status=final, updated_at=now()))
    return status(job_id)
