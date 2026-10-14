"""Stage L1: daily auto refresh lifecycle + read-side freshness status.

Scope: daily / end-of-day light-layer research data ONLY (daily_quotes,
valuations, institutional_daily, market_summary via market_daily.update_daily()
/ market_daily.backfill_history()). This is NOT an intraday/live quote service --
it never touches app.datasources.mis_snapshot / app.datasources.fugle, and it
never marks a date "completed" from anything but those two existing functions.

This module owns orchestration (when to call the pipeline, retry bounds,
concurrency locking, freshness reporting) but never duplicates the pipeline's
write logic -- every actual data write still goes through market_daily.py.
"""
import datetime as dt
import fcntl
import hashlib
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from app import config
from app.db import engine, query_all, query_one, upsert_many
from app.db.tables import daily_refresh_runs
from app.datasources import twse

log = logging.getLogger(__name__)

# Combined twse+tpex row-count floor below which a "completed" fetch is
# actually incomplete. Mirrors the "全市場正常 1000+ 檔" idiom already used by
# twse.resolve_latest_trading_date() and history_backfill's per-market minimums
# (500 twse / 400 tpex), just applied to the two markets' upserted total.
MIN_COMBINED_QUOTE_ROWS = 800

# Stage L1.1: how far back of the target we look for an INTERNAL hole (a missing
# session strictly between an older local_max and target, hidden by MAX(date)
# alone once a later date exists). Bounded so a hole check is never unbounded.
HOLE_CHECK_LOOKBACK_DAYS = 10


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _tz() -> ZoneInfo:
    return ZoneInfo(config.AUTO_REFRESH_TIMEZONE)


def now_local() -> dt.datetime:
    return dt.datetime.now(_tz())


@contextmanager
def refresh_lock():
    """Same flock-per-DB-path idiom as history_backfill.runner_lock() -- and,
    since Stage L1.1, the literal SAME lock file. Auto-refresh, manual
    update-daily/backfill AND the Strategy Lab 5-year historical backfill all
    write overlapping tables (daily_quotes, valuations); two locks that can't
    see each other would defeat the point, so they share one DB-writer
    coordination mechanism instead."""
    path = Path(engine.url.database).resolve() if engine.url.database != ":memory:" else Path(":memory:")
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:20]
    lockpath = config.DATA_DIR / f"db-writer-{digest}.lock"
    with lockpath.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another daily refresh is already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _resolve_target_trading_day() -> str | None:
    """Ground truth, ALWAYS re-checked against the real source: does the
    official source currently have a newer complete trading day than we do?
    Never guesses from weekday -- a holiday or an as-yet-unpublished afternoon
    simply yields no newer date, exactly like the existing
    market_daily.update_daily() gate. Every write-path caller (attempt_refresh,
    scheduler_tick, startup catch-up, manual update) goes through this
    uncached function -- only the READ-side status() uses the cached wrapper
    below, so a real refresh attempt is never fooled by a stale cache."""
    try:
        value = twse.resolve_latest_trading_date()
    except Exception as exc:
        log.warning("resolve_latest_trading_date failed: %s", exc)
        return None
    _target_cache["value"], _target_cache["at"] = value, time.monotonic()
    return value


# Stage L1.1: process-local TTL cache for the read-only status() path only, so
# a dashboard polling every 20s doesn't turn into a TWSE request every 20s.
# Populated as a side effect of any real (uncached) resolution above, so
# status() benefits immediately after any real attempt without waiting out
# its own TTL.
_target_cache: dict = {"value": None, "at": 0.0}


def _cached_target_trading_day() -> str | None:
    age = time.monotonic() - _target_cache["at"]
    if _target_cache["at"] and age < config.AUTO_REFRESH_STATUS_CACHE_SECONDS:
        return _target_cache["value"]
    return _resolve_target_trading_day()


def _local_max_quote_date(conn) -> str | None:
    return query_one(conn, "SELECT MAX(date) d FROM daily_quotes")["d"]


def _dataset_dates(conn) -> dict:
    return {
        "quotes": _local_max_quote_date(conn),
        "institutional": query_one(conn, "SELECT MAX(date) d FROM institutional_daily")["d"],
        "valuations": query_one(conn, "SELECT MAX(date) d FROM valuations")["d"],
        "market_summary": query_one(conn, "SELECT MAX(date) d FROM market_summary")["d"],
        # The Strategy Lab research benchmark (TAIEX_TR total-return series) is a
        # SEPARATE table with a SEPARATE pipeline (history_backfill.py only) --
        # never conflated with the daily light-layer's market_summary/TAIEX price
        # card. See status()'s research_benchmark block.
        "research_benchmark": query_one(
            conn, "SELECT MAX(date) d FROM benchmark_series WHERE benchmark_id='TAIEX_TR'")["d"],
    }


def _session_dates_in_window(conn, start_exclusive: str, end_inclusive: str) -> list[str] | None:
    """Real trading sessions strictly after start_exclusive through end_inclusive,
    from trading_calendar (populated by the Strategy Lab backfill). Returns None
    when the calendar has no coverage for this window -- callers must then fall
    back to a weekday-candidate list (never treated as final truth for a WRITE
    decision without further confirmation; see _confirm_real_holiday)."""
    if start_exclusive >= end_inclusive:
        return []
    rows = query_all(conn, "SELECT date FROM trading_calendar WHERE date > :a AND date <= :b ORDER BY date",
                     a=start_exclusive, b=end_inclusive)
    return [r["date"] for r in rows] if rows else None


def _weekday_candidates(start_exclusive: str, end_inclusive: str) -> list[str]:
    dates = []
    d = dt.date.fromisoformat(start_exclusive) + dt.timedelta(days=1)
    end = dt.date.fromisoformat(end_inclusive)
    while d <= end:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += dt.timedelta(days=1)
    return dates


def _missing_sessions_for_write(conn, local_max: str, target: str) -> tuple[list[str], str]:
    """The write-path's session-accurate gap: real trading sessions between
    local_max and target that daily_quotes doesn't have, over a bounded lookback
    window so a stale-but-already-far-behind local_max can't blow this up.
    MAX(date) alone cannot see a hole that sits BEFORE a later date that IS
    present (e.g. Monday missing, Tuesday present) -- this checks presence per
    session, not just the max.

    When trading_calendar has no coverage, falls back to a weekday-candidate
    list -- deliberately NOT confirmed against the network (an earlier version
    of this function did a per-candidate official-evidence probe here, but that
    ran from attempt_refresh()'s fast "is there anything to do" path, called on
    every scheduler tick, and a slow/unreachable network turned that into a
    hang; see docs/history/stage-l1-1-daily-refresh-hardening-report.md §1). A real
    holiday sitting in the gap without trading_calendar coverage can therefore
    cost a few harmless extra retries (bounded by the retry budget, always
    idempotent) before settling into 'failed' with the date visible -- safe,
    since the failure mode is an honest 'not verified', never a false 'fresh'.

    The bounded lookback (HOLE_CHECK_LOOKBACK_DAYS) only ever WIDENS the window
    when trading_calendar has real coverage there -- widening it for the
    weekday-guess fallback would flag every date before local_max we simply
    never attempted (out of scope, not a hole) as "missing", a false positive.
    The weekday fallback therefore only ever looks forward from local_max."""
    widened_start = min(local_max, (dt.date.fromisoformat(target) - dt.timedelta(days=HOLE_CHECK_LOOKBACK_DAYS)).isoformat())
    sessions = _session_dates_in_window(conn, widened_start, target)
    if sessions is not None:
        have = {r["date"] for r in query_all(
            conn, "SELECT DISTINCT date FROM daily_quotes WHERE date > :a AND date <= :b",
            a=widened_start, b=target)}
        return [d for d in sessions if d not in have], "trading_calendar"
    have = {r["date"] for r in query_all(
        conn, "SELECT DISTINCT date FROM daily_quotes WHERE date > :a AND date <= :b",
        a=local_max, b=target)}
    return [d for d in _weekday_candidates(local_max, target) if d not in have], "weekday_estimate"


def _local_hole_check(conn, target: str) -> tuple[bool, str]:
    """Read-side (status()), NO network: true if trading_calendar has evidence
    of a missing session in the lookback window; 'unverified' when the calendar
    has no coverage -- status() must not silently call that 'fresh' when it
    hasn't actually verified it, but it also must not probe the network on
    every 20s poll (see attempt_refresh()/the write path for the real check).
    Same widen-only-with-real-evidence rule as _missing_sessions_for_write."""
    local_max = _local_max_quote_date(conn)
    if local_max is None or not target:
        return False, "none"
    widened_start = min(local_max, (dt.date.fromisoformat(target) - dt.timedelta(days=HOLE_CHECK_LOOKBACK_DAYS)).isoformat())
    sessions = _session_dates_in_window(conn, widened_start, target)
    if sessions is None:
        return False, "unverified"
    have = {r["date"] for r in query_all(
        conn, "SELECT DISTINCT date FROM daily_quotes WHERE date > :a AND date <= :b",
        a=widened_start, b=target)}
    return bool(set(sessions) - have), "trading_calendar"


def _pending_missing_dates(conn, local_max: str | None, target: str | None) -> dict:
    """Read-side display list for status() -- no network. Prefers trading_calendar
    (real evidence); falls back to a clearly-labelled weekday estimate when the
    calendar has no coverage. Purely informational; the write-path decision
    (_missing_sessions_for_write) is the one that gates completed/partial."""
    if not target or not local_max or local_max >= target:
        return {"dates": [], "source": "none"}
    sessions = _session_dates_in_window(conn, local_max, target)
    if sessions is not None:
        return {"dates": sessions, "source": "trading_calendar"}
    return {"dates": _weekday_candidates(local_max, target), "source": "weekday_estimate"}


def _retry_rows_for(conn, target: str) -> list[dict]:
    return query_all(conn, "SELECT * FROM daily_refresh_runs WHERE target_date=:t "
                     "AND status IN ('failed','partial') ORDER BY started_at", t=target)


def _retry_budget_exhausted(conn, target: str) -> tuple[bool, int]:
    rows = _retry_rows_for(conn, target)
    if not rows:
        return False, 0
    if len(rows) >= config.AUTO_REFRESH_MAX_RETRIES:
        return True, len(rows)
    first = dt.datetime.fromisoformat(rows[0]["started_at"])
    age_minutes = (dt.datetime.now(dt.timezone.utc) - first).total_seconds() / 60
    return age_minutes > config.AUTO_REFRESH_RETRY_WINDOW_MINUTES, len(rows)


def _has_prior_completed(conn, target: str) -> bool:
    return bool(query_one(conn, "SELECT 1 FROM daily_refresh_runs WHERE target_date=:t "
                          "AND status='completed' LIMIT 1", t=target))


def _last_run_for_target(conn, target: str) -> dict | None:
    return query_one(conn, "SELECT * FROM daily_refresh_runs WHERE target_date=:t "
                     "ORDER BY started_at DESC LIMIT 1", t=target)


def recover_interrupted() -> int:
    """A crash mid-attempt leaves a daily_refresh_runs row stuck at 'running'
    forever (same class of problem as the four *_lab.py recover_interrupted()
    calls in app/main.py's lifespan). Probe the lock: if nobody actually holds
    it, any 'running' row is stale and gets marked failed, never left stuck and
    never silently treated as still in progress."""
    with engine.begin() as conn:
        stuck = query_all(conn, "SELECT id FROM daily_refresh_runs WHERE status='running'")
    if not stuck:
        return 0
    try:
        with refresh_lock():
            pass
    except RuntimeError:
        return 0  # a run is genuinely in progress right now; leave it alone
    finished = _now_utc()
    with engine.begin() as conn:
        for row in stuck:
            conn.execute(daily_refresh_runs.update().where(daily_refresh_runs.c.id == row["id"])
                         .values(status="failed", finished_at=finished,
                                 error="interrupted: process restarted"))
    return len(stuck)


def _classify_update_daily(result: dict, target: str) -> str:
    if not result or result.get("error"):
        return "failed"
    if result.get("trade_date") != target:
        return "failed"
    if (result.get("quotes") or 0) < MIN_COMBINED_QUOTE_ROWS:
        return "partial"
    return "completed"


def _watchlist_deep_refresh():
    from app.services import deep_fetch
    try:
        from app.identity import system_scope
        with system_scope(), engine.begin() as conn:
            watch = query_all(conn, "SELECT DISTINCT stock_id FROM watchlist")
        for r in watch:
            deep_fetch.ensure_deep(r["stock_id"])
    except Exception as exc:
        log.error("watchlist deep refresh failed: %s", exc)


def _record_attempt(trigger: str, target: str, fetch) -> dict:
    """fetch() must return (result_dict, status). Shared log bookkeeping for
    every attempt (startup/scheduler/manual) so the freshness API sees them all."""
    run_id = uuid.uuid4().hex
    started = _now_utc()
    with engine.begin() as conn:
        upsert_many(conn, daily_refresh_runs, [{
            "id": run_id, "trigger": trigger, "target_date": target,
            "status": "running", "started_at": started, "finished_at": None,
            "result_json": None, "error": None,
        }])
        was_completed_before = _has_prior_completed(conn, target)

    result, error, status = None, None, "failed"
    try:
        result, status = fetch()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]
        status = "failed"

    finished = _now_utc()
    with engine.begin() as conn:
        upsert_many(conn, daily_refresh_runs, [{
            "id": run_id, "trigger": trigger, "target_date": target,
            "status": status, "started_at": started, "finished_at": finished,
            "result_json": json.dumps(result, ensure_ascii=False) if result is not None else None,
            "error": error,
        }])

    # Stage L1.1: pre-L1 scheduler behavior was "run watchlist deep-fetch after
    # every scheduled daily job" -- never a side effect of a manual click. Keep
    # that exact scope (auto-driven triggers only) rather than silently
    # expanding what a manual "立即更新" button does; deep-fetch's own
    # success/failure never feeds back into this attempt's status.
    if status == "completed" and not was_completed_before and trigger != "manual":
        _watchlist_deep_refresh()
    return {"attempted": True, "run_id": run_id, "status": status,
            "target_date": target, "result": result, "error": error}


def _run_and_record(trigger: str, target: str, local_max: str, missing: list[str]) -> dict:
    """Auto-refresh path (startup/scheduler): the catch-up strategy is chosen
    from the COUNT of actual missing trading sessions (Stage L1.1), never a raw
    calendar-day gap -- a calendar-day threshold can't tell "one session missing"
    from "two sessions missing 3 calendar days apart because a holiday sits in
    between", which is exactly how an internal hole used to slip through. More
    than one missing session reuses the existing multi-day light backfill
    instead of only ever fetching "latest"; either way, completion is verified
    afterward by re-checking for missing sessions, not by MAX(date) alone."""
    from app.services import market_daily

    def fetch():
        if len(missing) <= 1:
            result = market_daily.update_daily()
        else:
            span_days = (dt.date.fromisoformat(target) - dt.date.fromisoformat(missing[0])).days + 2
            result = market_daily.backfill_history(days=min(span_days, 400))
        with engine.begin() as conn:
            still_missing, _source = _missing_sessions_for_write(conn, local_max, target)
        if still_missing:
            return result, "partial"
        if len(missing) <= 1:
            return result, _classify_update_daily(result, target)
        return result, "completed"

    return _record_attempt(trigger, target, fetch)


def attempt_refresh(trigger: str) -> dict:
    """Shared entry point for startup catch-up and the periodic scheduler tick.
    Idempotent: safe to call repeatedly (already-fresh / exhausted-retry /
    already-locked all return without writing). "Already fresh" is decided by
    actual missing trading sessions (Stage L1.1), not just MAX(daily_quotes.date)
    >= target -- a later date already present cannot hide an earlier hole."""
    if not config.AUTO_REFRESH_ENABLED:
        return {"attempted": False, "reason": "auto_refresh_disabled"}
    target = _resolve_target_trading_day()
    if target is None:
        return {"attempted": False, "reason": "source_unavailable"}
    with engine.begin() as conn:
        local_max = _local_max_quote_date(conn)
    if local_max is None:
        # Fresh install: no baseline to catch up from. Auto-refresh must not
        # silently kick off a large historical backfill on its own -- that is
        # the documented, explicit first-time-setup step, not "daily catch-up".
        return {"attempted": False, "reason": "no_baseline_data", "target_date": target}
    with engine.begin() as conn:
        missing, source = _missing_sessions_for_write(conn, local_max, target)
        last_run = _last_run_for_target(conn, target)
    # A session can be "present" (>=1 row, so not a hole) but still short of the
    # row-count floor from a prior partial fetch -- _missing_sessions_for_write
    # only sees presence, so also honor the last recorded status for target.
    still_incomplete = bool(last_run and last_run["status"] == "partial")
    if not missing and not still_incomplete:
        return {"attempted": False, "reason": "already_fresh", "target_date": target}
    with engine.begin() as conn:
        exhausted, retry_count = _retry_budget_exhausted(conn, target)
    if exhausted:
        return {"attempted": False, "reason": "retry_budget_exhausted",
                "target_date": target, "retry_count": retry_count, "missing_sessions": missing}
    try:
        with refresh_lock():
            return _run_and_record(trigger, target, local_max, missing)
    except RuntimeError:
        return {"attempted": False, "reason": "locked", "target_date": target}


def run_manual_update() -> dict:
    """Manual '立即更新今日行情' -- unchanged single-day pipeline call
    (market_daily.update_daily()), just serialized against auto-refresh and
    logged the same way an auto attempt would be, so it shows up in freshness
    status too. Never escalates to the multi-day backfill the way the
    auto-refresh catch-up path does -- that stays an explicit user action."""
    from app.services import market_daily
    with refresh_lock():
        target = _resolve_target_trading_day()

        def fetch():
            result = market_daily.update_daily()
            return result, (_classify_update_daily(result, target) if target else "failed")

        if target:
            return _record_attempt("manual", target, fetch)
        # Source unreachable: still run the pipeline so the button behaves as
        # it always has, just without a target_date to log against.
        return {"attempted": True, "run_id": None, "status": "failed",
                "target_date": None, "result": market_daily.update_daily(), "error": None}


def run_manual_backfill(days: int) -> dict:
    """Manual '回填 N 天歷史' -- unchanged market_daily.backfill_history(),
    just serialized against auto-refresh/manual-update via the same lock."""
    from app.services import market_daily
    with refresh_lock():
        return market_daily.backfill_history(days)


def scheduler_tick():
    try:
        result = attempt_refresh("scheduler")
        if result.get("attempted"):
            log.info("auto refresh tick: %s", result)
    except Exception as exc:
        log.error("auto refresh tick failed: %s", exc)


def _startup_catchup_safe():
    try:
        result = attempt_refresh("startup_catchup")
        log.info("startup catch-up: %s", result)
    except Exception as exc:
        log.error("startup catch-up failed: %s", exc)


def startup_catchup_async() -> threading.Thread | None:
    """Never block server startup: catch-up runs on a daemon thread."""
    if not config.AUTO_REFRESH_ENABLED:
        return None
    thread = threading.Thread(target=_startup_catchup_safe, daemon=True, name="startup-catchup")
    thread.start()
    return thread


def status() -> dict:
    """Unified read-side freshness contract. Never a single boolean.

    Stage L1.1: uses the process-local cached target (see _cached_target_trading_day)
    so a dashboard polling every 20s does not turn into an external TWSE request
    every 20s -- an actual refresh attempt (scheduler/startup/manual) always
    re-resolves for real. Internal-hole detection here is LOCAL ONLY (trading_calendar,
    no network): when the calendar has no coverage for the window, freshness falls
    back to the plain MAX(date) comparison and 'internal_hole_check' is reported as
    'unverified' rather than silently claiming a verified 'fresh'."""
    recover_interrupted()
    with engine.begin() as conn:
        dates = _dataset_dates(conn)
        last_success = query_one(conn, "SELECT * FROM daily_refresh_runs WHERE status='completed' "
                                 "ORDER BY started_at DESC LIMIT 1")
        last_attempt = query_one(conn, "SELECT * FROM daily_refresh_runs ORDER BY started_at DESC LIMIT 1")
        current_run = query_one(conn, "SELECT * FROM daily_refresh_runs WHERE status='running' "
                                "ORDER BY started_at DESC LIMIT 1")
        target = _cached_target_trading_day()
        pending = _pending_missing_dates(conn, dates["quotes"], target)
        last_for_target = _last_run_for_target(conn, target) if target else None
        has_hole, hole_source = _local_hole_check(conn, target) if target else (False, "none")
        retry_count = 0
        latest_error = None
        exhausted = False
        if target:
            exhausted, retry_count = _retry_budget_exhausted(conn, target)
            failed_rows = _retry_rows_for(conn, target)
            if failed_rows:
                latest_error = failed_rows[-1]["error"]

    local_max = dates["quotes"]
    still_incomplete = bool(last_for_target and last_for_target["status"] == "partial") or has_hole
    if current_run:
        freshness = "partial" if local_max and target and local_max >= target else "stale"
        current_status = "running"
    elif local_max is None:
        freshness, current_status = "stale", "idle"
    elif target is None:
        freshness, current_status = "unknown", "idle"
    elif local_max >= target:
        freshness = "partial" if still_incomplete else "fresh"
        current_status = "idle"
    elif exhausted:
        freshness, current_status = "failed", "idle"
    else:
        freshness, current_status = "stale", "idle"

    return {
        "now": now_local().isoformat(timespec="seconds"),
        "timezone": config.AUTO_REFRESH_TIMEZONE,
        "expected_trading_day": target,
        "latest_completed_quote_date": dates["quotes"],
        "latest_institutional_date": dates["institutional"],
        "latest_valuation_date": dates["valuations"],
        "latest_market_summary_date": dates["market_summary"],
        "freshness": freshness,
        "internal_hole_detected": has_hole,
        "internal_hole_check_source": hole_source,
        "current_status": current_status,
        "current_run": current_run,
        "last_successful_refresh": last_success,
        "last_attempted_refresh": last_attempt,
        "pending_missing_dates": pending["dates"],
        "pending_missing_dates_source": pending["source"],
        "retry_count_for_target": retry_count,
        "retry_budget_exhausted": exhausted,
        "latest_error": latest_error,
        "auto_refresh": {
            "enabled": config.AUTO_REFRESH_ENABLED,
            "check_time": config.AUTO_REFRESH_CHECK_TIME,
            "poll_interval_minutes": config.AUTO_REFRESH_POLL_INTERVAL_MINUTES,
            "max_retries": config.AUTO_REFRESH_MAX_RETRIES,
            "retry_window_minutes": config.AUTO_REFRESH_RETRY_WINDOW_MINUTES,
            "status_cache_seconds": config.AUTO_REFRESH_STATUS_CACHE_SECONDS,
        },
        # Stage L1.1 §3: the Strategy Lab research benchmark (TAIEX_TR total-return
        # series, app.services.strategy.panel's default benchmark_id) is a SEPARATE
        # table/pipeline from the light-layer market_summary/TAIEX price card above.
        # Daily auto-refresh never touches it -- only the Strategy Lab 5-year
        # historical backfill (history_backfill.py) does, on its own, separate cadence.
        # This global "freshness" field is about the light layer ONLY; it must never
        # be read as "the research benchmark is current too".
        "research_benchmark": {
            "benchmark_id": "TAIEX_TR",
            "latest_date": dates["research_benchmark"],
            "updated_by": "history_backfill.py (Strategy Lab historical backfill) only",
            "tracked_by_daily_auto_refresh": False,
        },
    }


def public_status() -> dict:
    """Authenticated member view: freshness facts, no runner logs/errors/config."""
    full = status()
    keys = ('freshness', 'expected_trading_day', 'latest_completed_quote_date',
            'latest_institutional_date', 'latest_valuation_date',
            'latest_market_summary_date', 'internal_hole_detected',
            'internal_hole_check_source', 'current_status', 'research_benchmark')
    return {key: full[key] for key in keys}
