"""Stage L1: daily auto refresh lifecycle + freshness status. Offline only --
twse.resolve_latest_trading_date() and market_daily.update_daily()/
backfill_history() are always mocked; never hits a real network endpoint."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from app.db import upsert_many, query_one
from app.db import tables as t
from app.db.migrations import migrate
from app.services import data_freshness as fresh
from app.services import history_backfill as jobs


class FreshnessTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/test.db")
        migrate(self.engine)
        self.patches = [
            patch.object(fresh, "engine", self.engine),
            patch.object(fresh.config, "DATA_DIR", Path(self.tmp.name)),
            patch.object(fresh.config, "AUTO_REFRESH_ENABLED", True),
            patch.object(fresh.config, "AUTO_REFRESH_MAX_RETRIES", 3),
            patch.object(fresh.config, "AUTO_REFRESH_RETRY_WINDOW_MINUTES", 180),
            # Stage L1.1: the read-side target-trading-day cache is process-global
            # (deliberately, to cut external calls -- see _cached_target_trading_day);
            # reset it per test so one test's resolved value can't leak into another's.
            patch.dict(fresh._target_cache, {"value": None, "at": 0.0}, clear=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def _set_quote_date(self, date):
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_quotes, [{"stock_id": "2330", "date": date, "close": 100}])

    def _fill_weekdays(self, start_exclusive, end_inclusive):
        """Simulate a real backfill_history()/update_daily() call that actually
        writes every weekday session in the range, not just the last date."""
        import datetime as _dt
        d = _dt.date.fromisoformat(start_exclusive) + _dt.timedelta(days=1)
        end = _dt.date.fromisoformat(end_inclusive)
        while d <= end:
            if d.weekday() < 5:
                self._set_quote_date(d.isoformat())
            d += _dt.timedelta(days=1)

    def _run_row(self, target):
        with self.engine.begin() as conn:
            return query_one(conn, "SELECT * FROM daily_refresh_runs WHERE target_date=:t "
                             "ORDER BY started_at DESC LIMIT 1", t=target)


class StartupCatchupTests(FreshnessTestBase):
    def test_fully_fresh_no_fetch(self):
        """Local data already matches the source's latest trading day: no fetch, no log row."""
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily") as upd:
            result = fresh.attempt_refresh("startup_catchup")
        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "already_fresh")
        upd.assert_not_called()
        self.assertIsNone(self._run_row("2026-09-25"))

    def test_holiday_no_newer_trading_day_no_fetch(self):
        """Source has no newer trading day than local (e.g. a holiday) -> no fetch.
        Never inferred from weekday; only from what the source actually has."""
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily") as upd, \
             patch("app.services.market_daily.backfill_history") as bf:
            result = fresh.attempt_refresh("scheduler")
        self.assertFalse(result["attempted"])
        upd.assert_not_called()
        bf.assert_not_called()

    def test_missing_single_trading_day_triggers_update_daily(self):
        """Local is one trading day behind -> single-day update_daily() pipeline runs."""
        self._set_quote_date("2026-09-24")

        def fake_update():
            self._set_quote_date("2026-09-25")
            return {"trade_date": "2026-09-25", "quotes": 1500}

        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily", side_effect=fake_update) as upd, \
             patch("app.services.market_daily.backfill_history") as bf:
            result = fresh.attempt_refresh("startup_catchup")
        upd.assert_called_once()
        bf.assert_not_called()
        self.assertTrue(result["attempted"])
        self.assertEqual(result["status"], "completed")
        row = self._run_row("2026-09-25")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["trigger"], "startup_catchup")

    def test_multi_day_gap_uses_backfill_history_not_update_daily(self):
        """A gap of several trading days (e.g. laptop asleep for a week) reuses the
        EXISTING multi-day light backfill instead of only ever fetching 'latest'."""
        self._set_quote_date("2026-09-10")

        def fake_backfill(days):
            self._fill_weekdays("2026-09-10", "2026-09-25")  # a real backfill fills every session
            return {"days_filled": 10, "days_skipped": 5}

        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily") as upd, \
             patch("app.services.market_daily.backfill_history", side_effect=fake_backfill) as bf:
            result = fresh.attempt_refresh("startup_catchup")
        bf.assert_called_once()
        upd.assert_not_called()
        self.assertEqual(result["status"], "completed")

    def test_internal_hole_not_fresh_calendar_evidence(self):
        """Stage L1.1 regression: local_max is Thursday, app restarts Tuesday with
        BOTH Monday and Tuesday missing (a holiday-adjacent gap makes the raw
        calendar-day span look small even though 2 sessions are missing). A fetch
        that only fills Tuesday (the old bug: calendar-day-gap<=3 -> update_daily()
        only) must NOT be reported 'completed' with trading_calendar as evidence."""
        with self.engine.begin() as conn:
            upsert_many(conn, t.trading_calendar, [{"date": d} for d in
                        ("2026-09-24", "2026-09-28", "2026-09-29")])  # Thu, Mon, Tue (Fri/weekend closed)
        self._set_quote_date("2026-09-24")  # Thursday

        def fake_backfill_only_fills_latest(days):
            self._set_quote_date("2026-09-29")  # incomplete: only fills Tuesday, leaves Monday a hole
            return {"days_filled": 1, "days_skipped": 0}

        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-29"), \
             patch("app.services.market_daily.update_daily") as upd, \
             patch("app.services.market_daily.backfill_history", side_effect=fake_backfill_only_fills_latest) as bf:
            result = fresh.attempt_refresh("startup_catchup")
        # 2 real missing sessions (Mon+Tue) -> must choose the multi-day path, not update_daily().
        bf.assert_called_once()
        upd.assert_not_called()
        self.assertNotEqual(result["status"], "completed")
        self.assertEqual(result["status"], "partial")  # backfill_history mock still only filled Tuesday

    def test_internal_hole_not_fresh_weekday_fallback(self):
        """Same Mon+Tue missing regression, but with NO trading_calendar coverage
        at all (weekday-estimate fallback, which -- without calendar evidence --
        also treats the intervening Friday as a candidate session, see
        _missing_sessions_for_write's documented tradeoff). Must still catch the
        real hole, not report 'fresh' just because MAX(date) reached Tuesday."""
        self._set_quote_date("2026-09-24")  # Thursday, no trading_calendar rows anywhere

        def fake_backfill_fills_everything(days):
            self._fill_weekdays("2026-09-24", "2026-09-29")  # Fri + Mon + Tue
            return {"days_filled": 3, "days_skipped": 0}

        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-29"), \
             patch("app.services.market_daily.update_daily") as upd, \
             patch("app.services.market_daily.backfill_history", side_effect=fake_backfill_fills_everything) as bf:
            result = fresh.attempt_refresh("startup_catchup")
        bf.assert_called_once()
        upd.assert_not_called()
        self.assertEqual(result["status"], "completed")
        with self.engine.begin() as conn:
            self.assertEqual(query_one(conn, "SELECT COUNT(DISTINCT date) n FROM daily_quotes "
                                       "WHERE date IN ('2026-09-25','2026-09-28','2026-09-29')")["n"], 3)

    def test_weekday_fallback_never_flags_dates_before_local_max(self):
        """The bounded lookback must only ever widen the window using REAL
        trading_calendar evidence -- without it, weekday-guessing backward past
        local_max would flag out-of-scope history as false-positive holes."""
        self._set_quote_date("2026-09-24")  # only one date on record; nothing before it was ever attempted
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   side_effect=lambda: (self._set_quote_date("2026-09-25"),
                                        {"trade_date": "2026-09-25", "quotes": 1500})[1]) as upd, \
             patch("app.services.market_daily.backfill_history") as bf:
            result = fresh.attempt_refresh("startup_catchup")
        upd.assert_called_once()  # single missing session (09-25), not a wrongly-widened multi-day gap
        bf.assert_not_called()
        self.assertEqual(result["status"], "completed")

    def test_no_baseline_data_never_auto_bootstraps(self):
        """A brand-new empty DB must NOT silently trigger a large historical
        backfill from auto-refresh -- that stays the documented manual first-time step."""
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily") as upd, \
             patch("app.services.market_daily.backfill_history") as bf:
            result = fresh.attempt_refresh("startup_catchup")
        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "no_baseline_data")
        upd.assert_not_called()
        bf.assert_not_called()

    def test_source_unavailable_no_fetch_no_crash(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value=None), \
             patch("app.services.market_daily.update_daily") as upd:
            result = fresh.attempt_refresh("scheduler")
        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "source_unavailable")
        upd.assert_not_called()


class IncompleteAndRetryTests(FreshnessTestBase):
    def test_incomplete_response_marked_partial_not_completed(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 12}):
            result = fresh.attempt_refresh("scheduler")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self._run_row("2026-09-25")["status"], "partial")

    def test_partial_day_is_retried_even_though_date_already_present(self):
        """A partial fetch already wrote SOME rows under target_date -- the next
        tick must not treat MAX(date)>=target as 'done', or a partial day would
        get silently stuck as fresh forever."""
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 12}):
            fresh.attempt_refresh("scheduler")
            self._set_quote_date("2026-09-25")  # partial fetch did write the date's row(s)
            result = fresh.attempt_refresh("scheduler")
        self.assertTrue(result["attempted"], "a still-partial date must keep being retried")

    def test_retry_bounded_by_max_attempts(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 1}):
            for _ in range(3):  # AUTO_REFRESH_MAX_RETRIES patched to 3
                r = fresh.attempt_refresh("scheduler")
                self.assertTrue(r["attempted"])
            blocked = fresh.attempt_refresh("scheduler")
        self.assertFalse(blocked["attempted"])
        self.assertEqual(blocked["reason"], "retry_budget_exhausted")

    def test_retry_bounded_by_time_window(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.config, "AUTO_REFRESH_RETRY_WINDOW_MINUTES", 0):
            with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
                 patch("app.services.market_daily.update_daily",
                       return_value={"trade_date": "2026-09-25", "quotes": 1}):
                fresh.attempt_refresh("scheduler")
                blocked = fresh.attempt_refresh("scheduler")
        self.assertFalse(blocked["attempted"])
        self.assertEqual(blocked["reason"], "retry_budget_exhausted")

    def test_failed_fetch_keeps_last_error(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily", side_effect=RuntimeError("boom")):
            fresh.attempt_refresh("scheduler")
            s = fresh.status()
        self.assertIn("boom", s["latest_error"])
        self.assertEqual(self._run_row("2026-09-25")["status"], "failed")


class IdempotencyAndConcurrencyTests(FreshnessTestBase):
    def test_same_date_idempotent_after_completion(self):
        self._set_quote_date("2026-09-24")

        def fake_update():
            self._set_quote_date("2026-09-25")
            return {"trade_date": "2026-09-25", "quotes": 1500}

        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily", side_effect=fake_update) as upd:
            first = fresh.attempt_refresh("scheduler")
            second = fresh.attempt_refresh("scheduler")
        self.assertEqual(first["status"], "completed")
        self.assertFalse(second["attempted"])
        self.assertEqual(second["reason"], "already_fresh")
        upd.assert_called_once()

    def test_manual_update_blocked_while_auto_refresh_holds_lock(self):
        with self.assertRaises(RuntimeError):
            with fresh.refresh_lock():
                fresh.run_manual_update()

    def test_auto_refresh_skips_quietly_when_manual_holds_lock(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            with fresh.refresh_lock():
                result = fresh.attempt_refresh("scheduler")
        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "locked")

    def test_admin_update_daily_route_returns_409_when_locked(self):
        from app.api import routes
        from fastapi import HTTPException
        with fresh.refresh_lock():
            with self.assertRaises(HTTPException) as ctx:
                routes.admin_update_daily()
        self.assertEqual(ctx.exception.status_code, 409)


class RestartSafetyTests(FreshnessTestBase):
    def test_stuck_running_row_from_crash_is_recovered(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_refresh_runs, [{
                "id": "stale-run", "trigger": "scheduler", "target_date": "2026-09-24",
                "status": "running", "started_at": fresh._now_utc(),
            }])
        n = fresh.recover_interrupted()
        self.assertEqual(n, 1)
        with self.engine.begin() as conn:
            row = query_one(conn, "SELECT * FROM daily_refresh_runs WHERE id='stale-run'")
        self.assertEqual(row["status"], "failed")
        self.assertIn("interrupted", row["error"])

    def test_genuinely_running_row_is_left_alone_while_lock_is_held(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_refresh_runs, [{
                "id": "live-run", "trigger": "scheduler", "target_date": "2026-09-24",
                "status": "running", "started_at": fresh._now_utc(),
            }])
        with fresh.refresh_lock():
            n = fresh.recover_interrupted()
        self.assertEqual(n, 0)
        with self.engine.begin() as conn:
            row = query_one(conn, "SELECT * FROM daily_refresh_runs WHERE id='live-run'")
        self.assertEqual(row["status"], "running")

    def test_status_self_heals_stuck_running_row(self):
        self._set_quote_date("2026-09-24")
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_refresh_runs, [{
                "id": "stale-run-2", "trigger": "startup_catchup", "target_date": "2026-09-24",
                "status": "running", "started_at": fresh._now_utc(),
            }])
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-24"):
            s = fresh.status()
        self.assertEqual(s["current_status"], "idle")
        self.assertIsNone(s["current_run"])


class ManualPathUnchangedTests(FreshnessTestBase):
    def test_manual_update_calls_same_pipeline_function(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 1500}) as upd:
            result = fresh.run_manual_update()
        upd.assert_called_once_with()
        self.assertEqual(result["result"], {"trade_date": "2026-09-25", "quotes": 1500})
        self.assertEqual(self._run_row("2026-09-25")["trigger"], "manual")

    def test_manual_backfill_is_a_thin_passthrough(self):
        with patch("app.services.market_daily.backfill_history",
                   return_value={"days_filled": 3, "days_skipped": 1}) as bf:
            result = fresh.run_manual_backfill(30)
        bf.assert_called_once_with(30)
        self.assertEqual(result, {"days_filled": 3, "days_skipped": 1})

    def test_manual_update_never_escalates_to_multiday_backfill(self):
        """Even with a multi-day gap, a manual click stays the single-day
        pipeline call -- only auto-refresh's catch-up path may escalate."""
        self._set_quote_date("2026-09-01")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 1500}) as upd, \
             patch("app.services.market_daily.backfill_history") as bf:
            fresh.run_manual_update()
        upd.assert_called_once()
        bf.assert_not_called()


class StatusContractTests(FreshnessTestBase):
    def test_never_a_single_boolean_has_required_fields(self):
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        for field in ("now", "expected_trading_day", "latest_completed_quote_date",
                      "latest_institutional_date", "latest_valuation_date", "latest_market_summary_date",
                      "freshness", "internal_hole_detected", "internal_hole_check_source",
                      "last_successful_refresh", "last_attempted_refresh",
                      "current_status", "pending_missing_dates", "latest_error", "auto_refresh",
                      "research_benchmark"):
            self.assertIn(field, s)
        self.assertIsInstance(s["freshness"], str)
        self.assertIn(s["freshness"], ("fresh", "partial", "stale", "failed", "unknown"))
        self.assertIn("benchmark_id", s["research_benchmark"])
        self.assertFalse(s["research_benchmark"]["tracked_by_daily_auto_refresh"])

    def test_fresh_state(self):
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["freshness"], "fresh")

    def test_stale_state_within_retry_budget(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["freshness"], "stale")

    def test_unknown_state_when_source_unreachable(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value=None):
            s = fresh.status()
        self.assertEqual(s["freshness"], "unknown")

    def test_failed_state_after_retry_budget_exhausted(self):
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 1}):
            for _ in range(3):
                fresh.attempt_refresh("scheduler")
            s = fresh.status()
        self.assertEqual(s["freshness"], "failed")

    def test_pending_missing_dates_uses_trading_calendar_when_available(self):
        self._set_quote_date("2026-09-22")
        with self.engine.begin() as conn:
            upsert_many(conn, t.trading_calendar, [{"date": d} for d in
                        ("2026-09-23", "2026-09-24", "2026-09-25")])
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["pending_missing_dates"], ["2026-09-23", "2026-09-24", "2026-09-25"])
        self.assertEqual(s["pending_missing_dates_source"], "trading_calendar")

    def test_pending_missing_dates_falls_back_to_labelled_weekday_estimate(self):
        self._set_quote_date("2026-09-22")  # Tuesday; no trading_calendar rows populated
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["pending_missing_dates_source"], "weekday_estimate")
        self.assertIn("2026-09-25", s["pending_missing_dates"])

    def test_intraday_or_future_date_is_never_treated_as_a_completed_daily_close(self):
        """resolve_latest_trading_date() is the only source of 'expected_trading_day'
        and it is real-source-backed (never a naive future/weekday guess); this module
        must never IMPORT the intraday/live-quote datasources at all (prose in
        comments/docstrings mentioning them by name to explain the boundary is fine)."""
        import ast
        source = Path(fresh.__file__).read_text()
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{n.name}" for n in node.names)
            elif isinstance(node, ast.Import):
                imported.update(n.name for n in node.names)
        self.assertFalse(any("mis_snapshot" in m for m in imported), imported)
        self.assertFalse(any("fugle" in m for m in imported), imported)


class SchedulerWindowTests(unittest.TestCase):
    def test_poll_window_derived_from_config_not_hardcoded(self):
        from app import scheduler as sched
        with patch.object(sched.config, "AUTO_REFRESH_CHECK_TIME", "15:05"), \
             patch.object(sched.config, "AUTO_REFRESH_RETRY_WINDOW_MINUTES", 180):
            start_hour, end_hour = sched._daily_refresh_window()
        self.assertEqual(start_hour, 15)
        self.assertGreaterEqual(end_hour, 18)


class CrossRunnerLockingTests(FreshnessTestBase):
    """Stage L1.1 §2: Stage L1 daily refresh and the Strategy Lab 5-year historical
    backfill both write daily_quotes/valuations -- they must share one DB-writer
    coordination mechanism (the same flock file), not two locks blind to each other."""

    def setUp(self):
        super().setUp()
        extra = [patch.object(jobs, "engine", self.engine),
                 patch.object(jobs.config, "DATA_DIR", Path(self.tmp.name))]
        for p in extra:
            p.start()
        self.patches.extend(extra)

    def test_history_backfill_lock_blocks_daily_refresh_lock(self):
        with jobs.runner_lock():
            with self.assertRaises(RuntimeError):
                with fresh.refresh_lock():
                    pass

    def test_daily_refresh_lock_blocks_history_backfill_lock(self):
        with fresh.refresh_lock():
            with self.assertRaises(ValueError):
                with jobs.runner_lock():
                    pass

    def test_both_locks_use_the_same_file(self):
        import hashlib
        digest = hashlib.sha256(str(Path(self.engine.url.database).resolve()).encode()).hexdigest()[:20]
        expected = Path(self.tmp.name) / f"db-writer-{digest}.lock"
        with fresh.refresh_lock():
            self.assertTrue(expected.exists())
        with jobs.runner_lock():
            self.assertTrue(expected.exists())

    def test_manual_update_blocked_while_history_backfill_runs(self):
        """The admin route surface too: a running Strategy Lab backfill must
        block a concurrent manual '立即更新今日行情', not silently race it."""
        with jobs.runner_lock():
            with self.assertRaises(RuntimeError):
                fresh.run_manual_update()

    def test_scheduler_tick_skips_quietly_while_history_backfill_runs(self):
        self._set_quote_date("2026-09-24")
        with jobs.runner_lock(), \
             patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            result = fresh.attempt_refresh("scheduler")
        self.assertFalse(result["attempted"])
        self.assertEqual(result["reason"], "locked")


class StatusUpstreamTrafficTests(FreshnessTestBase):
    """Stage L1.1 §4: GET /api/admin/data-freshness must not turn a 20s dashboard
    poll into a 20s TWSE poll."""

    def test_repeated_status_reads_share_one_cached_resolution(self):
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25") as resolve:
            for _ in range(10):
                fresh.status()
        self.assertEqual(resolve.call_count, 1)

    def test_cache_expires_after_ttl(self):
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.config, "AUTO_REFRESH_STATUS_CACHE_SECONDS", 0), \
             patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25") as resolve:
            fresh.status()
            fresh.status()
        self.assertEqual(resolve.call_count, 2)

    def test_a_real_attempt_always_resolves_uncached(self):
        """The write path must never be fooled by the read-side cache: even
        immediately after a cached status() read, attempt_refresh() re-resolves."""
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date",
                          side_effect=["2026-09-24", "2026-09-25"]) as resolve:
            fresh.status()  # populates the cache with "2026-09-24" -> already fresh
            with patch("app.services.market_daily.update_daily",
                       side_effect=lambda: (self._set_quote_date("2026-09-25"),
                                            {"trade_date": "2026-09-25", "quotes": 1500})[1]):
                result = fresh.attempt_refresh("scheduler")
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(result["status"], "completed")


class WatchlistDeepFetchScopeTests(FreshnessTestBase):
    """Stage L1.1 §5: pre-Stage-L1 scheduler behavior ran watchlist deep-fetch
    after every scheduled daily job, never as a side effect of a manual click.
    Preserve that exact scope; deep-fetch's own success/failure never feeds the
    recorded daily_refresh_runs status either way."""

    def _add_watchlist_stock(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.watchlist, [{"stock_id": "2330", "tier": "research"}])

    def test_scheduler_completion_triggers_watchlist_deep_fetch(self):
        self._add_watchlist_stock()
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   side_effect=lambda: (self._set_quote_date("2026-09-25"),
                                        {"trade_date": "2026-09-25", "quotes": 1500})[1]), \
             patch("app.services.deep_fetch.ensure_deep") as ensure_deep:
            fresh.attempt_refresh("scheduler")
        ensure_deep.assert_called_once_with("2330")

    def test_startup_catchup_completion_triggers_watchlist_deep_fetch(self):
        self._add_watchlist_stock()
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   side_effect=lambda: (self._set_quote_date("2026-09-25"),
                                        {"trade_date": "2026-09-25", "quotes": 1500})[1]), \
             patch("app.services.deep_fetch.ensure_deep") as ensure_deep:
            fresh.attempt_refresh("startup_catchup")
        ensure_deep.assert_called_once_with("2330")

    def test_manual_completion_does_not_trigger_watchlist_deep_fetch(self):
        """A manual '立即更新' click must not gain a new side effect it never had
        before Stage L1 -- deep-fetch stays exclusively tied to the auto-driven
        (scheduler/startup) path, matching the pre-Stage-L1 scheduler contract."""
        self._add_watchlist_stock()
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   return_value={"trade_date": "2026-09-25", "quotes": 1500}), \
             patch("app.services.deep_fetch.ensure_deep") as ensure_deep:
            fresh.run_manual_update()
        ensure_deep.assert_not_called()

    def test_deep_fetch_failure_does_not_change_recorded_status(self):
        self._add_watchlist_stock()
        self._set_quote_date("2026-09-24")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"), \
             patch("app.services.market_daily.update_daily",
                   side_effect=lambda: (self._set_quote_date("2026-09-25"),
                                        {"trade_date": "2026-09-25", "quotes": 1500})[1]), \
             patch("app.services.deep_fetch.ensure_deep", side_effect=RuntimeError("boom")):
            result = fresh.attempt_refresh("scheduler")
        self.assertEqual(result["status"], "completed")


class BenchmarkFreshnessTests(FreshnessTestBase):
    """Stage L1.1 §3: the Strategy Lab research benchmark (TAIEX_TR, benchmark_series)
    is a separate table/pipeline from the light-layer market_summary -- daily
    auto-refresh must never claim it, and global 'freshness' must stay truthful
    about the light layer only."""

    def test_research_benchmark_not_claimed_fresh_by_daily_refresh(self):
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["freshness"], "fresh")  # light layer IS fresh
        self.assertIsNone(s["research_benchmark"]["latest_date"])  # benchmark_series untouched
        self.assertFalse(s["research_benchmark"]["tracked_by_daily_auto_refresh"])

    def test_research_benchmark_date_reads_taiex_tr_only(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.benchmark_series, [
                {"benchmark_id": "TAIEX_TR", "date": "2026-09-20", "value": 100.0},
                {"benchmark_id": "SOME_OTHER_INDEX", "date": "2026-09-25", "value": 999.0},
            ])
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["research_benchmark"]["latest_date"], "2026-09-20")

    def test_market_summary_field_not_mislabelled_as_research_benchmark(self):
        """Stage L1 mistakenly named this field latest_benchmark_date, sourced
        from market_summary (the daily TAIEX price card) -- easily confused with
        the Strategy Lab TAIEX_TR research benchmark. Stage L1.1 renames it and
        keeps the two clearly separate fields."""
        with self.engine.begin() as conn:
            upsert_many(conn, t.market_summary, [{"date": "2026-09-25", "taiex_close": 20000.0}])
        self._set_quote_date("2026-09-25")
        with patch.object(fresh.twse, "resolve_latest_trading_date", return_value="2026-09-25"):
            s = fresh.status()
        self.assertEqual(s["latest_market_summary_date"], "2026-09-25")
        self.assertNotIn("latest_benchmark_date", s)
        self.assertIsNone(s["research_benchmark"]["latest_date"])


if __name__ == "__main__":
    unittest.main()
