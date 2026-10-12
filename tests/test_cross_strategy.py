"""Stage G tests: cross-strategy analysis / complementarity.

Most of this suite is pure (hand-built ``StrategyData``/``FoldData`` fixtures, no DB) because
every analytical function in ``app.services.cross_strategy`` except ``data.py`` takes already
loaded data. One integration case (``CrossStrategyDataTests``) runs a real, tiny Walk Forward
job against a temp SQLite DB — same fixture style as ``test_walkforward_backend.py`` — to check
``data.py``'s use of the real ``walkforward_lab`` accessors and its missing-job handling.
"""
import datetime as dt
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).parent))
from test_optimization_backend import RANGE_PATCH, SMALL, SPACE, STOCKS  # noqa: E402

from app.db import upsert_many  # noqa: E402
from app.db import tables as t  # noqa: E402
from app.db.migrations import migrate  # noqa: E402
from app.services import cross_strategy as cs  # noqa: E402
from app.services import optimization_lab as opt  # noqa: E402
from app.services import strategy_lab as lab  # noqa: E402
from app.services import walkforward_lab as wfl  # noqa: E402
from app.services.cross_strategy import (  # noqa: E402
    behavior, candidate_availability, correlation, data, drawdown, exposure, overlap, sleeve,
)
from app.services.optimization import search_space as ss  # noqa: E402

D0, D1 = "2024-01-01", "2024-08-15"
DATA_START, PANEL_START = "2024-02-01", "2024-01-01"
PROTOCOL = {"initial_train_months": 3, "test_months": 2, "step_months": 2, "min_final_test_months": 1}


def _days(start, end):
    d, out = dt.date.fromisoformat(start), []
    while d.isoformat() <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# ── fixtures for the pure tests ──────────────────────────────────────────────
def _points(dates, values, benchmark=None):
    return [{"date": d, "net_equity": v, "gross_equity": v, "benchmark": (benchmark[i] if benchmark else None)}
            for i, (d, v) in enumerate(zip(dates, values))]


def _fold(index, outcome, equity, fills=None, oos_metrics=None, test_window=("2024-01-02", "2024-01-09")):
    return data.FoldData(
        index=index, outcome=outcome, windows={"train": ["2023-01-01", "2023-12-31"],
                                                "test": list(test_window), "truncated": False},
        oos_metrics=oos_metrics or {"sharpe": 1.0, "cagr": 10.0, "max_drawdown": -5.0, "turnover": 100.0,
                                    "cost_drag_pct": 0.1, "net_return": 5.0},
        oos={"unresolved_action_count": 0, "partial_rebalance_count": 0, "effective_first_fill_date": test_window[0]},
        severity={"max_severity": "caution", "counts": {"info": 0, "caution": 1, "critical": 0}},
        candidate_digest="digest-x", primary={"point_key": "k", "params": {"a": 1}},
        equity=equity, fills=fills or [])


def _strategy(key, folds, job_id="job-" + "x"):
    return data.StrategyData(key=key, job_id=f"{job_id}-{key}", template_key=key,
                             provenance={"strategy_version_id": 1, "strategy_version_no": 1},
                             diagnostics={"stub": True}, folds={f.index: f for f in folds})


DATES_0 = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
DATES_1 = ["2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06", "2024-02-07", "2024-02-08"]


def _no_banned_keys(obj, banned=("winner", "best", "recommended", "rank")):
    """Recursively assert no output KEY claims a best/winning/ranked strategy (mirrors the
    check ``test_optimization_backend.py`` runs on Optimization output)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in banned:
                return False
            if not _no_banned_keys(v, banned):
                return False
        return True
    if isinstance(obj, list):
        return all(_no_banned_keys(v, banned) for v in obj)
    return True


class CorrelationTests(unittest.TestCase):
    def test_perfectly_correlated_series_gives_correlation_one(self):
        equity = [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000]
        bench = [1_500_000, 1_490_000, 1_495_000, 1_480_000, 1_470_000, 1_460_000]
        a = _strategy("a", [_fold(0, "evaluated", _points(DATES_0, equity, bench))])
        b = _strategy("b", [_fold(0, "evaluated", _points(DATES_0, equity, bench))])
        result = correlation.correlation_matrix({"a": a, "b": b})
        pair = result["pairs"][0]
        self.assertEqual(pair["n_dates"], len(DATES_0) - 1)   # first date has no prior -> no return
        self.assertAlmostEqual(pair["correlation"], 1.0, places=9)
        self.assertEqual(pair["folds"], [0])

    def test_date_alignment_only_uses_overlapping_calendar_dates(self):
        eq_a = [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000]
        eq_b = [2_000_000, 2_020_000, 2_010_000, 2_040_000, 2_060_000, 2_030_000]
        a = _strategy("a", [_fold(0, "evaluated", _points(DATES_0, eq_a))])
        # b only has data for a strict subset of a's dates (simulating a shorter evaluated fold)
        b = _strategy("b", [_fold(0, "evaluated", _points(DATES_0[:4], eq_b[:4]))])
        series_a = correlation.strategy_return_series(a)
        series_b = correlation.strategy_return_series(b)
        common = sorted(set(series_a) & set(series_b))
        self.assertEqual(common, DATES_0[1:4])   # exactly the overlap, nothing invented
        result = correlation._pair_correlation(series_a, series_b)
        self.assertIsNone(result["correlation"])   # below MIN_OVERLAP=5 -> withheld, not a fake number
        self.assertEqual(result["n_dates"], 3)

    def test_downside_correlation_restricts_to_benchmark_down_days(self):
        eq_a = [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000, 1_040_000, 1_035_000]
        eq_b = [1_000_000, 995_000, 1_002_000, 990_000, 985_000, 998_000, 1_010_000, 1_005_000]
        bench = [1_500_000, 1_510_000, 1_490_000, 1_480_000, 1_470_000, 1_500_000, 1_520_000, 1_510_000]
        dates = DATES_0 + ["2024-01-10", "2024-01-11"]
        a = _strategy("a", [_fold(0, "evaluated", _points(dates, eq_a, bench))])
        b = _strategy("b", [_fold(0, "evaluated", _points(dates, eq_b, bench))])
        full = correlation.correlation_matrix({"a": a, "b": b})["pairs"][0]
        down = correlation.downside_correlation_matrix({"a": a, "b": b})["pairs"][0]
        bench_returns = [bench[i] / bench[i - 1] - 1.0 for i in range(1, len(bench))]
        down_dates = {dates[i + 1] for i, r in enumerate(bench_returns) if r < 0}
        self.assertEqual(down["n_dates"], len(down_dates))
        self.assertLessEqual(down["n_dates"], full["n_dates"])

    def test_fold_boundary_is_never_bridged(self):
        """A strategy with two folds where the raw equity level jumps hugely across the boundary
        (fold 1 restarts from ~1,000,000 after fold 0 ended near 5,000,000) must not produce a
        return for that jump: the first date of fold 1 is dropped, not diffed against fold 0."""
        eq0 = [1_000_000, 1_500_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000]
        eq1 = [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000]
        sd = _strategy("a", [_fold(0, "evaluated", _points(DATES_0, eq0), test_window=(DATES_0[0], DATES_0[-1])),
                             _fold(1, "evaluated", _points(DATES_1, eq1), test_window=(DATES_1[0], DATES_1[-1]))])
        series = correlation.strategy_return_series(sd)
        self.assertEqual(len(series), (len(DATES_0) - 1) + (len(DATES_1) - 1))
        # the boundary "return" (eq1[0] / eq0[-1] - 1) would be wildly negative; assert it's absent
        bogus = eq1[0] / eq0[-1] - 1.0
        self.assertTrue(all(abs(ret - bogus) > 1e-6 for ret, _ in series.values()))
        # every returned value is tagged with the fold it actually came from
        self.assertEqual({fold for _, fold in series.values()}, {0, 1})

    def test_no_candidate_cash_fold_is_excluded_not_zero_correlated(self):
        cash = _points(DATES_0, [1_000_000] * len(DATES_0))
        a = _strategy("a", [_fold(0, "no_candidate", cash)])
        b = _strategy("b", [_fold(0, "evaluated", _points(DATES_0, [1_000_000, 1_010_000, 1_005_000,
                                                                    1_020_000, 1_030_000, 1_015_000]))])
        self.assertEqual(correlation.strategy_return_series(a), {})
        result = correlation.correlation_matrix({"a": a, "b": b})
        self.assertIn("a", result["degenerate_or_empty_series_excluded_from_input"])
        # "a" contributes no return series at all, so no pair can be formed for it —
        # never a fake 0.0 correlation with a cash (zero-variance) strategy.
        self.assertEqual(result["pairs"], [])


class DrawdownTests(unittest.TestCase):
    def test_episode_detection_and_recovery(self):
        # peak 100, -8% trough, recovers to new high
        equity = [100, 100, 92, 95, 101, 99]
        dates = DATES_0
        pts = _points(dates, equity)
        eps = drawdown.episodes(pts, threshold=-0.05)
        self.assertEqual(len(eps), 1)
        ep = eps[0]
        self.assertEqual(ep["trough_date"], dates[2])
        self.assertAlmostEqual(ep["trough_dd"], -0.08, places=6)
        self.assertTrue(ep["recovered"])
        self.assertEqual(ep["recovery_date"], dates[4])

    def test_unrecovered_episode_at_fold_end(self):
        equity = [100, 95, 90, 80]
        pts = _points(DATES_0[:4], equity)
        eps = drawdown.episodes(pts, threshold=-0.05)
        self.assertEqual(len(eps), 1)
        self.assertFalse(eps[0]["recovered"])
        self.assertIsNone(eps[0]["recovery_date"])

    def test_drawdown_never_crosses_fold_boundary(self):
        """Fold 1 starts far below fold 0's peak; that must not register as a continuing
        drawdown of fold 0 — each fold's peak restarts at its own first date."""
        eq0 = [100, 110, 120]
        eq1 = [10, 10.5, 11]   # restart at a much lower cash base; not a -90% drawdown of fold 0
        pts0, pts1 = _points(DATES_0[:3], eq0), _points(DATES_1[:3], eq1)
        self.assertEqual(drawdown.episodes(pts0), [])   # monotonic up, no drawdown
        self.assertEqual(drawdown.episodes(pts1), [])   # monotonic up from its OWN peak of 10


class OverlapTests(unittest.TestCase):
    def test_holdings_reconstruction_and_jaccard(self):
        equity = _points(["2024-01-02"], [1_000_000])
        fills = [
            {"date": "2024-01-02", "stock_id": "1101", "side": "buy", "shares": 100, "reference_notional": 100_000},
            {"date": "2024-01-02", "stock_id": "1102", "side": "buy", "shares": 200, "reference_notional": 200_000},
        ]
        holdings = overlap.holdings_by_date(equity, fills)["2024-01-02"]
        self.assertEqual(set(holdings), {"1101", "1102"})
        self.assertAlmostEqual(holdings["1101"]["weight"], 0.1)

        fills_b = [
            {"date": "2024-01-02", "stock_id": "1102", "side": "buy", "shares": 50, "reference_notional": 50_000},
            {"date": "2024-01-02", "stock_id": "1103", "side": "buy", "shares": 50, "reference_notional": 50_000},
        ]
        holdings_b = overlap.holdings_by_date(equity, fills_b)["2024-01-02"]
        self.assertEqual(overlap.pairwise_jaccard(holdings, holdings_b), 1 / 3)

    def test_sell_removes_position(self):
        equity = _points(["2024-01-02", "2024-02-01"], [1_000_000, 1_000_000])
        fills = [
            {"date": "2024-01-02", "stock_id": "1101", "side": "buy", "shares": 100, "reference_notional": 100_000},
            {"date": "2024-02-01", "stock_id": "1101", "side": "sell", "shares": 100, "reference_notional": 0},
        ]
        snaps = overlap.holdings_by_date(equity, fills)
        self.assertEqual(set(snaps["2024-01-02"]), {"1101"})
        self.assertEqual(set(snaps["2024-02-01"]), set())

    def test_monthly_snapshot_skips_no_candidate_folds(self):
        equity = _points(["2024-01-02"], [1_000_000])
        fills = [{"date": "2024-01-02", "stock_id": "1101", "side": "buy", "shares": 10,
                  "reference_notional": 10_000}]
        sd = _strategy("a", [_fold(0, "evaluated", equity, fills),
                             _fold(1, "no_candidate", _points(["2024-02-01"], [1_000_000]))])
        snaps = overlap.monthly_snapshots(sd)
        self.assertEqual(list(snaps), ["2024-01"])   # fold 1 contributes nothing


class SleeveContractTests(unittest.TestCase):
    def test_no_candidate_sleeve_is_all_cash_and_valid(self):
        sd = _strategy("a", [_fold(0, "no_candidate", _points(DATES_0, [1_000_000] * len(DATES_0)))])
        sl = sleeve.build(sd, 0)
        self.assertTrue(all(w == 1.0 for w in sl["cash_weight_by_date"].values()))
        self.assertEqual(sl["target_holdings_by_rebalance_date"], {})
        self.assertEqual(sleeve.validate(sl), [])

    def test_evaluated_sleeve_has_holdings_and_is_valid(self):
        equity = _points(DATES_0, [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000])
        fills = [{"date": DATES_0[0], "stock_id": "1101", "side": "buy", "shares": 100,
                  "reference_notional": 100_000}]
        sd = _strategy("a", [_fold(0, "evaluated", equity, fills)])
        sl = sleeve.build(sd, 0)
        self.assertIn(DATES_0[0], sl["target_holdings_by_rebalance_date"])
        self.assertEqual(sleeve.validate(sl), [])
        self.assertEqual(sl["evidence_type"], "independent_fold")

    def test_validate_catches_missing_field(self):
        sd = _strategy("a", [_fold(0, "no_candidate", _points(DATES_0, [1_000_000] * len(DATES_0)))])
        sl = sleeve.build(sd, 0)
        del sl["turnover"]
        problems = sleeve.validate(sl)
        self.assertIn("missing field: turnover", problems)

    def test_build_all_is_deterministic(self):
        equity = _points(DATES_0, [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000])
        fills = [{"date": DATES_0[0], "stock_id": "1101", "side": "buy", "shares": 100,
                  "reference_notional": 100_000}]
        strategies = {"a": _strategy("a", [_fold(0, "evaluated", equity, fills)]),
                     "b": _strategy("b", [_fold(0, "no_candidate", _points(DATES_0, [1_000_000] * 6))])}
        r1 = json.dumps(sleeve.build_all(strategies), sort_keys=True)
        r2 = json.dumps(sleeve.build_all(strategies), sort_keys=True)
        self.assertEqual(r1, r2)
        self.assertTrue(sleeve.build_all(strategies)["all_valid"])


class CandidateAvailabilityTests(unittest.TestCase):
    def test_active_count_and_missing_fold(self):
        cash = _points(DATES_0, [1_000_000] * len(DATES_0))
        equity = _points(DATES_0, [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000])
        strategies = {
            "a": _strategy("a", [_fold(0, "evaluated", equity), _fold(1, "no_candidate", cash)]),
            "b": _strategy("b", [_fold(0, "no_candidate", cash)]),   # fold 1 missing entirely
        }
        result = candidate_availability.analyze(strategies)
        fold0, fold1 = result["per_fold"]
        self.assertEqual(fold0["active_strategy_count"], 1)
        self.assertEqual(fold1["missing_from_job"], ["b"])
        self.assertEqual(fold1["active_strategy_count"], 0)
        self.assertFalse(fold1["all_active_no_candidate"])   # b is missing, not no_candidate

    def test_no_fold_has_zero_denominator_crash_with_empty_strategies(self):
        self.assertEqual(candidate_availability.analyze({})["per_fold"], [])


class FoldWindowAndMissingStrategyTests(unittest.TestCase):
    def test_mismatched_fold_window_is_flagged(self):
        eq = _points(DATES_0, [1_000_000] * len(DATES_0))
        a = _strategy("a", [_fold(0, "evaluated", eq, test_window=("2024-01-02", "2024-01-09"))])
        b = _strategy("b", [_fold(0, "evaluated", eq, test_window=("2024-02-01", "2024-02-08"))])
        check = cs._fold_window_check({"a": a, "b": b})
        self.assertEqual(len(check["mismatches"]), 1)
        self.assertIsNone(check["windows_by_fold"][0])

    def test_aligned_fold_window_has_no_mismatch(self):
        eq = _points(DATES_0, [1_000_000] * len(DATES_0))
        a = _strategy("a", [_fold(0, "evaluated", eq)])
        b = _strategy("b", [_fold(0, "evaluated", eq)])
        check = cs._fold_window_check({"a": a, "b": b})
        self.assertEqual(check["mismatches"], [])

    def test_load_all_reports_missing_job_without_raising(self):
        loaded, failed = data.load_all({"real": None, "bogus": "does-not-exist"})
        self.assertEqual(loaded, {})
        self.assertIn("real", failed)
        self.assertIn("bogus", failed)


class BehaviorComparisonTests(unittest.TestCase):
    def test_compare_never_ranks(self):
        eq = _points(DATES_0, [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_030_000, 1_015_000])
        strategies = {"a": _strategy("a", [_fold(0, "evaluated", eq)]),
                     "b": _strategy("b", [_fold(0, "no_candidate", _points(DATES_0, [1_000_000] * 6))])}
        result = behavior.compare(strategies)
        self.assertTrue(_no_banned_keys(result))




# ── one integration test against a real (tiny, temp) Walk Forward job ───────
class CrossStrategyDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/wf.db")
        migrate(self.engine)
        self.patches = [patch.object(opt, "engine", self.engine), patch.object(lab, "engine", self.engine),
                        patch.object(wfl, "engine", self.engine), patch.object(data, "engine", self.engine),
                        patch.dict(ss.ALLOWED["momentum_trend"], RANGE_PATCH)]
        for p in self.patches:
            p.start()
        dates = _days(D0, D1)
        rng, rows = random.Random(7), []
        drift = {"1101": .0008, "1102": .0003, "1103": -.0002}
        for s in STOCKS:
            px = 100.0
            for d in dates:
                px *= 1 + drift[s] + rng.gauss(0, .01)
                rows.append({"stock_id": s, "date": d, "open": px, "close": px,
                             "high": px * 1.01, "low": px * .99, "volume": 1e6, "amount": 1e8})
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": s, "name": s, "market": "TWSE", "type": "stock",
                                       "industry": f"sector-{s}"} for s in STOCKS])
            upsert_many(c, t.trading_calendar, [{"date": d} for d in dates])
            upsert_many(c, t.daily_quotes, rows)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def _run_job(self):
        payload = {"template_key": "momentum_trend", "params": SMALL, "stock_ids": STOCKS,
                  "data_start": DATA_START, "data_end": D1, "panel_start": PANEL_START,
                  "search_space": SPACE, "protocol": PROTOCOL}
        q = wfl.create_job(payload)
        if q["queued"]:
            wfl.execute_job(q["job_id"])
        return q["job_id"]

    def test_load_strategy_and_sector_map_against_real_job(self):
        job_id = self._run_job()
        sd = data.load_strategy("momentum_trend", job_id)
        self.assertEqual(sd.job_id, job_id)
        self.assertTrue(sd.folds)
        sectors = data.sector_map(set(STOCKS) | {"9999"})
        self.assertEqual(sectors["1101"], "sector-1101")
        self.assertIsNone(sectors["9999"])   # unknown id -> None, not a crash

    def test_analyze_handles_one_real_and_one_missing_strategy(self):
        job_id = self._run_job()
        result = cs.analyze({"momentum_trend": job_id, "ghost_strategy": "no-such-job"})
        self.assertEqual(result["loaded"], ["momentum_trend"])
        self.assertIn("ghost_strategy", result["failed"])
        codes = {w["code"] for w in result["warnings"]}
        self.assertIn("MISSING_STRATEGY_DATA", codes)
        # single-strategy correlation/overlap is structurally empty, not an error
        self.assertEqual(result["correlation"]["full_sample"]["pairs"], [])
        self.assertTrue(result["sleeve_contract"]["all_valid"])

    def test_route_is_thin_and_read_only(self):
        from fastapi.testclient import TestClient

        from app.main import app
        job_id = self._run_job()
        with TestClient(app) as client:
            r = client.post("/api/strategy-lab/cross-strategy/analyze",
                            json={"jobs": {"momentum_trend": job_id}})
            bad = client.post("/api/strategy-lab/cross-strategy/analyze", json={"jobs": {}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["loaded"], ["momentum_trend"])
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
