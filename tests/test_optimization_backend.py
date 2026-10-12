"""Stage E1 tests: optimization search space, robustness math, job lifecycle.

Offline (temp SQLite, synthetic data).  The real research window/data are not touched.
"""
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db import query_all, query_one, upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services import optimization_lab as opt
from app.services import strategy_lab as lab
from app.services.optimization import robustness as rb
from app.services.optimization import search_space as ss
from app.services.strategy import run_backtest
from app.services.strategy.panel import build_panel
from app.services.strategy.signals import TemplateSignalRule
from app.services.strategy.templates import TEMPLATES

START, END = "2025-01-02", "2025-02-28"
WINDOW = "2025-01-20"
SMALL = {
    "universe": {"liquidity_lookback": 3, "min_liquidity_observations": 3,
                 "min_average_daily_amount": 1},
    "momentum": {"lookback": 5, "skip_recent": 1},
    "trend": {"ma_lookback": 5},
    "selection": {"top_n": 2},
    "rebalance": {"frequency": "weekly"},
}
STOCKS = ["1101", "1102", "1103"]
SPACE = {"axes": {"momentum.lookback": [3, 5], "trend.ma_lookback": [3, 5],
                  "selection.top_n": [1, 2]}}
# The tiny synthetic DB cannot support the production minimum lookbacks.
RANGE_PATCH = {"momentum.lookback": ss._int(2, 252), "trend.ma_lookback": ss._int(2, 250),
               "selection.top_n": ss._int(1, 100)}


def _dates():
    d, out = dt.date.fromisoformat(START), []
    while d.isoformat() <= END:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _no_key(obj, banned=("winner", "best", "recommended", "optimal")):
    """Recursively assert no output key claims a best/winning configuration."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert not any(b in str(k).lower() for b in banned), f"banned key {k}"
            _no_key(v, banned)
    elif isinstance(obj, list):
        for v in obj:
            _no_key(v, banned)


# ── pure: search space ──────────────────────────────────
class SearchSpaceTests(unittest.TestCase):
    def test_weight_combinations_are_normalized_and_not_scaled_duplicates(self):
        combos = ss.weight_combinations(["a", "b", "c"], .2, .2)
        self.assertEqual(len(combos), 6)
        for c in combos:
            self.assertAlmostEqual(sum(c.values()), 1.0, places=9)
            self.assertTrue(all(v >= .2 - 1e-9 for v in c.values()))
        self.assertEqual(len({tuple(c.values()) for c in combos}), 6)
        self.assertEqual(len(ss.weight_combinations(["a", "b"], .1, .1)), 9)

    def test_grid_expansion_size_order_and_constraint(self):
        with patch.dict(ss.ALLOWED["momentum_trend"], RANGE_PATCH):
            space = ss.normalize_space("momentum_trend", {
                "axes": {"momentum.lookback": [3, 5], "momentum.skip_recent": [0, 3, 5],
                         "selection.top_n": [10, 20]}})
            points, skipped = ss.expand_grid(space, {})
        # lookback 3: skip 0 only; lookback 5: skip 0,3 -> 3 valid x 2 top_n
        self.assertEqual((len(points), skipped), (6, 6))
        self.assertTrue(all(p["momentum.lookback"] > p["momentum.skip_recent"] for p in points))
        again, _ = ss.expand_grid(space, {})
        self.assertEqual(points, again)

    def test_group_axes_expand_with_scalars(self):
        space = ss.normalize_space("value_quality", None)
        pts, _ = ss.expand_grid(space, {})
        self.assertEqual(len(pts), 48)
        for p in pts:
            self.assertAlmostEqual(p["factor_weights.value"] + p["factor_weights.quality"], 1.0)
            self.assertAlmostEqual(p["value.weights.earnings_yield"] + p["value.weights.book_yield"], 1.0)

    def test_default_spaces_are_small_and_valid_for_all_templates(self):
        self.assertEqual(set(ss.DEFAULT_SPACES), set(TEMPLATES))
        for key in TEMPLATES:
            p = ss.plan(key, {})
            self.assertLessEqual(p["grid_size"], 48, key)
            self.assertFalse(p["sampled"])
            base = ss.flatten(TEMPLATES[key].clone_config())
            for point in p["points"]:  # every default point resolves through the real validator
                lab.validate_definition(key, ss.to_overrides(point), {})
            self.assertTrue(set(ss.default_space(key)["axes"]) <= set(ss.ALLOWED[key]))
            self.assertTrue(base)

    def test_rejects_non_searchable_and_out_of_range(self):
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"axes": {"universe.min_average_daily_amount": [1]}})
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"axes": {"momentum.lookback": [500]}})
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"axes": {"factor_weights": [1]}})
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"weight_groups": {"quality_weights": {}}})
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"axes": {"selection.top_n": []}})
        with self.assertRaises(ValueError):
            ss.normalize_space("momentum_trend", {"axes": {"rebalance.frequency": ["daily"]}})

    def test_explicit_combinations_validated_and_deduped(self):
        pts = ss.normalize_explicit("momentum_trend", [
            {"momentum": {"lookback": 126}, "selection": {"top_n": 20}},
            {"momentum.lookback": 126, "selection.top_n": 20},
            {"factor_weights": {"momentum": .6, "trend": .4}}])
        self.assertEqual(len(pts), 2)
        with self.assertRaises(ValueError):
            ss.normalize_explicit("momentum_trend", [{"factor_weights": {"momentum": .6, "trend": .6}}])
        with self.assertRaises(ValueError):
            ss.normalize_explicit("momentum_trend", [{"factor_weights": {"momentum": 1.0}}])
        with self.assertRaises(ValueError):
            ss.normalize_explicit("momentum_trend", [{"momentum.lookback": 21, "momentum.skip_recent": 30}])
        with self.assertRaises(ValueError):
            ss.normalize_explicit("momentum_trend", [{"bogus": 1}])

    def test_budget_sampling_is_seeded_and_ordered(self):
        a = ss.plan("momentum_trend", {"max_runs": 10, "seed": 7})
        b = ss.plan("momentum_trend", {"max_runs": 10, "seed": 7})
        c = ss.plan("momentum_trend", {"max_runs": 10, "seed": 8})
        self.assertEqual((a["grid_size"], len(a["points"]), a["sampled"]), (36, 10, True))
        self.assertEqual(a["points"], b["points"])
        self.assertNotEqual(a["points"], c["points"])
        full, _ = ss.expand_grid(ss.default_space("momentum_trend"), {})
        idx = [full.index(p) for p in a["points"]]
        self.assertEqual(idx, sorted(idx))
        with self.assertRaises(ValueError):
            ss.plan("momentum_trend", {"max_runs": 10, "budget_policy": "reject"})
        with self.assertRaises(ValueError):
            ss.plan("momentum_trend", {"max_runs": ss.HARD_MAX_RUNS + 1})
        with self.assertRaises(ValueError):  # explicit combos are never silently truncated
            ss.plan("momentum_trend", {"max_runs": 1, "combinations": [
                {"selection.top_n": 10}, {"selection.top_n": 20}]})


# ── pure: robustness ────────────────────────────────────
def _grid(fn, n=5):
    topo = {"axes": {"a": list(range(1, n + 1)), "b": list(range(1, n + 1))}, "groups": {}}
    pts = [{"key": f"{a},{b}", "params": {"a": a, "b": b}, "value": fn(a, b), "metrics": {"cagr": fn(a, b)}}
           for a in range(1, n + 1) for b in range(1, n + 1)]
    return pts, topo


class RobustnessTests(unittest.TestCase):
    def label(self, fn, **kw):
        pts, topo = _grid(fn)
        return rb.analyze(pts, topo, **kw)["plateau"]

    def test_plateau_bowl(self):
        p = self.label(lambda a, b: 1 - ((a - 3) ** 2 + (b - 3) ** 2) / 50)
        self.assertEqual(p["label"], "plateau")
        self.assertEqual(p["peak_key"], "3,3")
        self.assertGreaterEqual(p["region_size"], 3)
        self.assertLessEqual(p["peak_drop"], .25)

    def test_isolated_peak(self):
        p = self.label(lambda a, b: 10.0 if (a, b) == (3, 3) else 0.0)
        self.assertEqual(p["label"], "isolated_peak")
        self.assertAlmostEqual(p["peak_drop"], 1.0)

    def test_flat_and_insufficient(self):
        self.assertEqual(self.label(lambda a, b: .5)["label"], "flat")
        pts, topo = _grid(lambda a, b: a)
        self.assertEqual(rb.analyze(pts[:4], topo)["plateau"]["label"], "insufficient_data")
        self.assertEqual(rb.analyze([], topo)["plateau"]["label"], "insufficient_data")

    def test_scattered(self):
        hi = {(5, 5), (5, 3), (3, 5), (4, 4), (3, 3)}
        vals = {(1, 1): 1.0, (1, 2): .9, (2, 1): .8, **{p: .95 for p in hi}}
        p = self.label(lambda a, b: vals.get((a, b), 0.0))
        self.assertEqual(p["label"], "scattered")
        self.assertEqual(p["region_size"], 2)

    def test_direction_flip_gives_same_classification(self):
        f = lambda a, b: 1 - ((a - 3) ** 2 + (b - 3) ** 2) / 50
        up = self.label(f)
        down = self.label(lambda a, b: -f(a, b), higher_is_better=False)
        self.assertEqual((up["label"], up["peak_key"]), (down["label"], down["peak_key"]))

    def test_boundary_flag_when_peak_on_edge(self):
        p = self.label(lambda a, b: a + b * 0.01)
        self.assertIn("a", p["edge_of_search_range"])

    def test_neighbours_and_weight_groups(self):
        topo = {"axes": {"n": [10, 20]}, "groups": {"w": {"keys": ["x", "y", "z"], "step": .2}}}
        combos = ss.weight_combinations(["x", "y", "z"], .2, .2)
        pts = []
        for n in (10, 20):
            for c in combos:
                params = {"n": n, **{f"w.{k}": v for k, v in c.items()}}
                pts.append({"key": ss.point_key(params), "params": params, "value": 1.0, "metrics": {}})
        g = rb.neighbour_graph(pts, topo)
        by = {p["key"]: p for p in pts}
        a = next(k for k, p in by.items() if p["params"] == {"n": 10, "w.x": .2, "w.y": .2, "w.z": .6})
        nbs = {tuple(by[k]["params"].values()) for k in g[a]}
        self.assertEqual(nbs, {(20, .2, .2, .6), (10, .2, .4, .4), (10, .4, .2, .4)})

    def test_axis_sensitivity_identifies_driving_axis(self):
        pts, topo = _grid(lambda a, b: float(a))
        s = rb.analyze(pts, topo)["axis_sensitivity"]
        self.assertEqual(s["ranking_by_step_sensitivity"][0], "a")
        self.assertAlmostEqual(s["by_parameter"]["b"]["normalized"], 0.0)
        self.assertGreater(s["by_parameter"]["a"]["normalized"], 0)

    def test_top_set_dispersion_and_parameters(self):
        pts, topo = _grid(lambda a, b: 1 - ((a - 3) ** 2 + (b - 3) ** 2) / 50)
        r = rb.analyze(pts, topo)
        d = r["top_set_dispersion"]["metrics"]["cagr"]
        self.assertEqual(d["n"], r["plateau"]["upper_set_size"])
        self.assertLessEqual(d["min"], d["median"])
        self.assertLessEqual(d["median"], d["max"])
        self.assertIn("a", r["top_set_parameters"])

    def test_surface_aggregation_keeps_spread(self):
        pts, topo = _grid(lambda a, b: a * 10 + b)
        s = rb.surface(pts, topo, "a", None, "median")
        cell = next(c for c in s["cells"] if c["x"] == 2)
        self.assertEqual((cell["n"], cell["min"], cell["max"], cell["value"]), (5, 21, 25, 23))
        s2 = rb.surface(pts, topo, "a", "b", "max")
        self.assertEqual(len(s2["cells"]), 25)
        with self.assertRaises(ValueError):
            rb.surface(pts, topo, "zzz")
        with self.assertRaises(ValueError):
            rb.surface(pts, topo, "a", "a")
        with self.assertRaises(ValueError):
            rb.surface(pts, topo, "a", None, "sum")

    def test_diagnostics_and_spearman(self):
        rows = [{"metrics": {"turnover": t_, "unresolved_action_count": u, "cost_drag_pct": 1.0,
                             "warnings_count": 2}} for t_, u in ((100, 0), (200, 3), (300, 1))]
        d = rb.diagnostics(rows, [1.0, 2.0, 3.0])
        self.assertEqual(d["runs_with_unresolved_actions"], 2)
        self.assertAlmostEqual(d["objective_turnover_spearman"], 1.0)
        self.assertEqual(d["unresolved_action_count"]["max"], 3)


# ── objective sorting ───────────────────────────────────
class ObjectiveTests(unittest.TestCase):
    def rows(self):
        return [{"run_index": 0, "metrics": {"sharpe": 1.0, "turnover": 300.0}},
                {"run_index": 1, "metrics": {"sharpe": 2.0, "turnover": 100.0}},
                {"run_index": 2, "metrics": {"sharpe": None, "turnover": 50.0}},
                {"run_index": 3, "metrics": {"sharpe": 2.0, "turnover": 200.0}},
                {"run_index": 4, "metrics": None}]

    def test_higher_is_better_with_stable_ties_and_missing_last(self):
        self.assertEqual([r["run_index"] for r in opt.sort_runs(self.rows(), "sharpe")], [1, 3, 0, 2, 4])

    def test_lower_is_better_objective(self):
        self.assertEqual([r["run_index"] for r in opt.sort_runs(self.rows(), "turnover")], [2, 1, 3, 0, 4])

    def test_unknown_objective(self):
        with self.assertRaises(ValueError):
            opt.sort_runs(self.rows(), "alpha_max")

    def test_no_recommendation_vocabulary_in_objectives(self):
        _no_key(opt.catalog())


# ── job lifecycle (temp DB) ─────────────────────────────
class JobCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/opt.db")
        migrate(self.engine)
        self.patches = [patch.object(opt, "engine", self.engine), patch.object(lab, "engine", self.engine),
                        patch.dict(ss.ALLOWED["momentum_trend"], RANGE_PATCH)]
        for p in self.patches:
            p.start()
        self.dates = _dates()
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": s, "name": s, "market": "TWSE", "type": "stock",
                                       "industry": "X"} for s in STOCKS])
            upsert_many(c, t.trading_calendar, [{"date": d} for d in self.dates])
            slope = {"1101": 1.0, "1102": .5, "1103": -.2}
            upsert_many(c, t.daily_quotes, [
                {"stock_id": s, "date": d, "open": 100 + slope[s] * i + (i % 3) * .3,
                 "close": 100 + slope[s] * i, "high": 101 + slope[s] * i, "low": 99 + slope[s] * i,
                 "volume": 1e6, "amount": 1e8}
                for s in STOCKS for i, d in enumerate(self.dates)])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def payload(self, **extra):
        return {"template_key": "momentum_trend", "params": SMALL, "stock_ids": STOCKS,
                "research_start": WINDOW, "panel_start": START, "research_end": END,
                "search_space": SPACE, **extra}

    def run_job(self, **extra):
        q = opt.create_job(self.payload(**extra))
        if q["queued"]:
            opt.execute_job(q["job_id"])
        return q["job_id"]

    def runs(self, job_id):
        with self.engine.connect() as c:
            return query_all(c, "SELECT * FROM optimization_runs WHERE job_id=:j ORDER BY run_index", j=job_id)


class JobTests(JobCase):
    def test_panel_loaded_once_for_whole_job(self):
        with patch.object(opt, "build_panel", wraps=build_panel) as spy:
            job = self.run_job()
        self.assertEqual(spy.call_count, 1)
        view = opt.get_job(job)
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["panel_loads"], 1)
        self.assertEqual((view["progress"]["total"], view["progress"]["completed"]), (8, 8))
        self.assertEqual(view["progress"]["fraction"], 1.0)

    def test_cache_reuse_skips_panel_and_engine(self):
        first = self.run_job()
        with patch.object(opt, "build_panel", wraps=build_panel) as panel_spy, \
                patch.object(opt, "run_backtest", wraps=run_backtest) as engine_spy:
            second = self.run_job()
        self.assertNotEqual(first, second)
        self.assertEqual(panel_spy.call_count, 0)
        self.assertEqual(engine_spy.call_count, 0)
        v = opt.get_job(second)
        self.assertEqual((v["status"], v["progress"]["cached"], v["panel_loads"]), ("completed", 8, 0))
        a, b = self.runs(first), self.runs(second)
        self.assertEqual([r["metrics_json"] for r in a], [r["metrics_json"] for r in b])
        self.assertTrue(all(r["cached_from_job"] == first for r in b))

    def test_cache_invalidated_by_data_change(self):
        self.run_job()
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE daily_quotes SET close=close+1 WHERE stock_id='1101' AND date='2025-02-10'")
        with patch.object(opt, "build_panel", wraps=build_panel) as spy:
            second = self.run_job()
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(opt.get_job(second)["progress"]["cached"], 0)

    def test_run_budget_and_seed(self):
        a = self.run_job(max_runs=3, seed=5)
        b = opt.create_job(self.payload(max_runs=3, seed=5))
        va = opt.get_job(a)
        self.assertEqual(va["progress"]["total"], 3)
        self.assertTrue(va["search"]["sampled"])
        self.assertEqual(va["search"]["grid_size"], 8)
        self.assertEqual([r["point_key"] for r in self.runs(a)], [r["point_key"] for r in self.runs(b["job_id"])])
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(max_runs=3, budget_policy="reject"))

    def test_partial_failure_does_not_kill_job(self):
        calls = {"n": 0}
        real = opt.evaluate_point

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("boom")
            return real(*a, **k)
        with patch.object(opt, "evaluate_point", side_effect=flaky):
            job = self.run_job()
        v = opt.get_job(job)
        self.assertEqual(v["status"], "completed")
        self.assertTrue(v["partial"])
        self.assertEqual((v["progress"]["completed"], v["progress"]["failed"]), (7, 1))
        failed = [r for r in self.runs(job) if r["status"] == "failed"]
        self.assertIn("boom", failed[0]["error"])
        rob = opt.get_robustness(job)
        self.assertEqual(rob["search_coverage"]["runs_failed"], 1)
        self.assertEqual(rob["search_coverage"]["runs_completed"], 7)

    def test_all_runs_failed_marks_job_failed(self):
        with patch.object(opt, "evaluate_point", side_effect=RuntimeError("x")):
            job = self.run_job()
        self.assertEqual(opt.get_job(job)["status"], "failed")

    def test_cancel_then_resume_never_recomputes_completed_runs(self):
        real, calls, holder = opt.evaluate_point, [], {}

        def counting(*a, **k):
            calls.append(1)
            if len(calls) == 3:
                opt.cancel_job(holder["id"])  # requested mid-run: this point still finishes
            return real(*a, **k)
        q = opt.create_job(self.payload())
        holder["id"] = q["job_id"]
        with patch.object(opt, "evaluate_point", side_effect=counting):
            opt.execute_job(q["job_id"])
        v = opt.get_job(q["job_id"])
        self.assertEqual((v["status"], v["progress"]["completed"], v["progress"]["pending"]), ("cancelled", 3, 5))
        with self.assertRaises(RuntimeError):
            opt.cancel_job(q["job_id"])
        opt.resume_job(q["job_id"])
        with patch.object(opt, "evaluate_point", side_effect=counting):
            opt.execute_job(q["job_id"])
        self.assertEqual(len(calls), 8)  # 3 + 5, nothing recomputed
        v = opt.get_job(q["job_id"])
        self.assertEqual((v["status"], v["progress"]["completed"]), ("completed", 8))

    def test_cancel_queued_job_is_immediate(self):
        q = opt.create_job(self.payload())
        self.assertEqual(opt.cancel_job(q["job_id"])["status"], "cancelled")
        opt.execute_job(q["job_id"])  # a cancelled job can no longer be claimed
        self.assertEqual(opt.get_job(q["job_id"])["progress"]["completed"], 0)

    def test_resume_after_job_level_failure_and_restart_recovery(self):
        q = opt.create_job(self.payload())
        with patch.object(opt, "build_panel", side_effect=RuntimeError("db down")):
            opt.execute_job(q["job_id"])
        v = opt.get_job(q["job_id"])
        self.assertEqual((v["status"], v["progress"]["pending"]), ("failed", 8))
        self.assertIn("db down", v["error"])
        opt.resume_job(q["job_id"])
        opt.execute_job(q["job_id"])
        self.assertEqual(opt.get_job(q["job_id"])["status"], "completed")
        # server died mid-run: job running, one point running
        q2 = opt.create_job(self.payload(seed=99, max_runs=4))
        with self.engine.begin() as c:
            c.exec_driver_sql(f"UPDATE optimization_jobs SET status='running' WHERE id='{q2['job_id']}'")
            c.exec_driver_sql(f"UPDATE optimization_runs SET status='running' WHERE job_id='{q2['job_id']}' AND run_index=0")
        self.assertEqual(opt.recover_interrupted(), 1)
        v = opt.get_job(q2["job_id"])
        self.assertEqual((v["status"], v["progress"]["running"], v["progress"]["pending"]), ("failed", 0, 4))
        opt.resume_job(q2["job_id"])
        opt.execute_job(q2["job_id"])
        self.assertEqual(opt.get_job(q2["job_id"])["status"], "completed")

    def test_resume_with_nothing_to_do_is_conflict(self):
        job = self.run_job()
        with self.assertRaises(RuntimeError):
            opt.resume_job(job)
        with self.assertRaises(RuntimeError):
            opt.resume_job(job, retry_failed=True)  # completed, no failed runs

    def test_retry_failed_reruns_only_failed(self):
        calls = {"n": 0}
        real = opt.evaluate_point

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("once")
            return real(*a, **k)
        with patch.object(opt, "evaluate_point", side_effect=flaky):
            job = self.run_job()
        opt.resume_job(job, retry_failed=True)
        with patch.object(opt, "evaluate_point", wraps=real) as spy:
            opt.execute_job(job)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(opt.get_job(job)["progress"]["completed"], 8)

    def test_deterministic_results_without_cache(self):
        a = self.run_job()
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE optimization_runs SET run_hash=NULL")  # defeat the cache
        with patch.object(opt, "run_backtest", wraps=run_backtest) as spy:
            b = self.run_job()
        self.assertEqual(spy.call_count, 8)
        ra, rb_ = self.runs(a), self.runs(b)
        self.assertTrue(all(r["cached_from_job"] is None for r in rb_))
        self.assertEqual([r["metrics_json"] for r in ra], [r["metrics_json"] for r in rb_])
        strip = lambda r: {k: v for k, v in json.loads(r["diagnostics_json"]).items() if k != "elapsed_seconds"}
        self.assertEqual([strip(r) for r in ra], [strip(r) for r in rb_])

    def test_optimization_does_not_change_execution_accounting(self):
        job = self.run_job(research_start=START)  # no windowing: must equal a plain run
        flat = json.loads(self.runs(job)[0]["params_json"])
        params = ss.to_overrides(flat)
        from app.services.strategy.signals import _deep_merge
        params, backtest, resolved = lab.validate_definition("momentum_trend", _deep_merge(SMALL, params), {})
        config = lab.build_backtest_config(backtest, "momentum_trend", params)
        panel = build_panel(START, END, STOCKS, publication_mode="assumed", db_engine=self.engine)
        direct = run_backtest(panel, TemplateSignalRule("momentum_trend", resolved, None), config)
        stored = json.loads(self.runs(job)[0]["metrics_json"])
        for key in ("total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "turnover",
                    "gross_return", "net_return"):
            self.assertEqual(stored[key], lab._clean(direct["metrics"][key]), key)
        # the optimizer hands the engine exactly the config a normal run would build
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE optimization_runs SET run_hash=NULL")  # force a real engine call
        with patch.object(opt, "run_backtest", wraps=run_backtest) as spy:
            self.run_job(seed=3, research_start=START, max_runs=1)
        cfg = spy.call_args.args[2]
        self.assertEqual(cfg.execution, lab.build_backtest_config({}, "momentum_trend",
                         _deep_merge(SMALL, ss.to_overrides(flat))).execution)
        self.assertEqual(cfg.costs, config.costs)

    def test_windowing_uses_fixed_dates_and_leaves_engine_output_alone(self):
        panel = build_panel(START, END, STOCKS, publication_mode="assumed", db_engine=self.engine)
        _, backtest, resolved = lab.validate_definition("momentum_trend", SMALL, {})
        config = lab.build_backtest_config(backtest, "momentum_trend", SMALL)
        result = run_backtest(panel, TemplateSignalRule("momentum_trend", resolved, None), config)
        full = opt.windowed_metrics(result, START)
        for k in ("total_return", "sharpe", "max_drawdown", "turnover"):
            self.assertEqual(full[k], result["metrics"][k])
        part = opt.windowed_metrics(result, WINDOW)
        self.assertEqual(part["_curve_points"], sum(1 for p in result["equity_curve"] if p["date"] >= WINDOW))
        self.assertNotEqual(part["total_return"], full["total_return"])
        with self.assertRaises(ValueError):
            opt.windowed_metrics(result, END)

    def test_unresolved_action_metadata_is_propagated(self):
        with self.engine.begin() as c:
            # cash/share effect unknown, applied while 1101 (strongest trend) is held
            upsert_many(c, t.corporate_actions, [
                {"stock_id": "1101", "date": "2025-02-12", "kind": "unknown", "source": "test",
                 "resolution": "unresolved_unknown_label"}])
        job = self.run_job(research_start=START)
        rows = self.runs(job)
        panel = build_panel(START, END, STOCKS, publication_mode="assumed", db_engine=self.engine)
        total = 0
        for r in rows:
            flat = json.loads(r["params_json"])
            from app.services.strategy.signals import _deep_merge
            params, backtest, resolved = lab.validate_definition(
                "momentum_trend", _deep_merge(SMALL, ss.to_overrides(flat)), {})
            config = lab.build_backtest_config(backtest, "momentum_trend", params)
            direct = run_backtest(panel, TemplateSignalRule("momentum_trend", resolved, None), config)
            diag = json.loads(r["diagnostics_json"])
            metrics = json.loads(r["metrics_json"])
            self.assertEqual(diag["unresolved_action_count"], direct["metadata"]["unresolved_action_count"])
            self.assertEqual(metrics["unresolved_action_count"], direct["metadata"]["unresolved_action_count"])
            self.assertEqual(diag["unresolved_actions_sample"], direct["metadata"]["unresolved_actions"][:10])
            self.assertEqual(diag["warnings_count"], len(direct["warnings"]))
            self.assertIn("coverage", diag)
            total += diag["unresolved_action_count"]
        self.assertGreater(total, 0, "fixture should hold 1101 across the event in some run")
        # an event before the research window still counts overall but not in-window
        job2 = self.run_job(research_start="2025-02-14", seed=1)
        d = json.loads(self.runs(job2)[0]["diagnostics_json"])
        self.assertEqual(d["unresolved_action_count_in_window"], 0)
        res = opt.get_results(job)
        flagged = [r for r in res["rows"] if r["unresolved_action_count"]]
        self.assertTrue(all("unresolved_actions" in r["flags"] for r in flagged))

    def test_results_show_risk_and_cost_with_every_ordering(self):
        job = self.run_job()
        for name in opt.OBJECTIVES:
            res = opt.get_results(job, objective=name)
            self.assertEqual(res["objective"]["name"], name)
            row = res["rows"][0]
            for k in ("max_drawdown", "turnover", "cost_drag_pct", "net_return", "sharpe", "cagr"):
                self.assertIn(k, row["summary"])
            self.assertIn("unresolved_action_count", row)
            self.assertIn("coverage", row)
            if res["rows"][0]["objective_value"] is not None:
                self.assertEqual(res["rows"][0]["exploration_rank"], 1)
            _no_key(res)
        by_sharpe = opt.get_results(job, objective="sharpe")["rows"]
        vals = [r["objective_value"] for r in by_sharpe if r["objective_value"] is not None]
        self.assertEqual(vals, sorted(vals, reverse=True))
        by_turn = opt.get_results(job, objective="turnover")["rows"]
        vals = [r["objective_value"] for r in by_turn if r["objective_value"] is not None]
        self.assertEqual(vals, sorted(vals))
        with self.assertRaises(ValueError):
            opt.get_results(job, objective="nope")
        page = opt.get_results(job, limit=3, offset=2)
        self.assertEqual((len(page["rows"]), page["total"]), (3, 8))

    def test_robustness_and_surface_outputs(self):
        job = self.run_job()
        rob = opt.get_robustness(job)
        _no_key(rob)
        self.assertIn(rob["analysis"]["plateau"]["label"],
                      {"plateau", "isolated_peak", "flat", "scattered", "insufficient_data"})
        self.assertEqual(rob["search_coverage"]["grid_fraction_evaluated"], 1.0)
        self.assertIn("rule", rob["analysis"])
        self.assertIn("turnover", rob["diagnostics"])
        self.assertEqual(rob["diagnostics"]["n_runs"], 8)
        self.assertTrue(rob["in_sample"])
        surf = opt.get_surface(job)
        _no_key(surf)
        self.assertEqual(len(surf["points"]), 8)
        self.assertEqual(surf["x"]["path"], "momentum.lookback")
        self.assertEqual(sum(c["n"] for c in surf["cells"]), 8)
        s1 = opt.get_surface(job, x="selection.top_n", y="trend.ma_lookback", agg="mean")
        self.assertEqual(len(s1["cells"]), 4)
        with self.assertRaises(ValueError):
            opt.get_surface(job, x="bogus")
        with self.assertRaises(ValueError):
            opt.get_robustness(job, top_fraction=0)

    def test_validation(self):
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(template_key="nope"))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(publication_mode="strict", template_key="value_quality",
                                        params={}, search_space=None))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(objective="best_ever"))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(research_start="2025-03-01"))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(panel_start="2025-02-01"))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(search_space=SPACE, combinations=[{"selection.top_n": 1}]))
        with self.assertRaises(LookupError):
            opt.get_job("missing")

    def test_active_duplicate_job_is_deduplicated(self):
        a = opt.create_job(self.payload())
        b = opt.create_job(self.payload())
        self.assertTrue(a["queued"])
        self.assertEqual((b["job_id"], b["queued"]), (a["job_id"], False))

    def test_explicit_combinations_job(self):
        job = self.run_job(search_space=None, combinations=[
            {"momentum.lookback": 3, "selection.top_n": 1}, {"momentum.lookback": 5, "selection.top_n": 2}])
        v = opt.get_job(job)
        self.assertEqual((v["search"]["mode"], v["progress"]["completed"]), ("explicit", 2))
        rob = opt.get_robustness(job)  # too few points -> explicit, non-committal label
        self.assertEqual(rob["analysis"]["plateau"]["label"], "insufficient_data")


class UniverseTests(JobCase):
    def test_monthly_union_includes_later_qualifiers(self):
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": "1104", "name": "late", "market": "TWSE",
                                       "type": "stock", "industry": "X"}])
            upsert_many(c, t.daily_quotes, [
                {"stock_id": "1104", "date": d, "open": 50, "close": 50, "high": 51, "low": 49,
                 "volume": 1e6, "amount": 1e8} for d in self.dates if d >= "2025-01-27"])
        with self.engine.connect() as c:
            ids, info = opt.monthly_universe_union(c, WINDOW, END, 1, 3)
            single = {r["stock_id"] for r in opt.data_foundation.liquid_universe(c, WINDOW, 1, 3)}
        self.assertNotIn("1104", single)
        self.assertIn("1104", ids)
        self.assertEqual([m["as_of"] for m in info["months"]], [WINDOW, "2025-02-03"])
        self.assertEqual(info["months"][1]["new_vs_previous_months"], 1)
        self.assertEqual(info["union_size"], 4)
        self.assertGreater(info["union_size"], info["first_month_size"])

    def test_job_without_stock_ids_freezes_union_and_uses_it_for_the_panel(self):
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": "1104", "name": "late", "market": "TWSE",
                                       "type": "stock", "industry": "X"}])
            upsert_many(c, t.daily_quotes, [
                {"stock_id": "1104", "date": d, "open": 50, "close": 50, "high": 51, "low": 49,
                 "volume": 1e6, "amount": 1e8} for d in self.dates if d >= "2025-01-27"])
        q = opt.create_job(self.payload(stock_ids=None, universe={"min_avg_amount": 1, "min_observations": 3}))
        with patch.object(opt, "build_panel", wraps=build_panel) as spy:
            opt.execute_job(q["job_id"])
        self.assertEqual(sorted(spy.call_args.args[2]), ["1101", "1102", "1103", "1104"])
        v = opt.get_job(q["job_id"])
        self.assertEqual(v["universe"]["kind"], "monthly_union")
        self.assertEqual(v["universe"]["union_size"], 4)
        self.assertEqual(v["request"]["stock_count"], 4)
        self.assertNotIn("stock_ids", v["universe"])
        self.assertEqual(v["coverage"]["stocks_requested"], 4)

    def test_empty_union_fails_job_and_is_resumable(self):
        q = opt.create_job(self.payload(stock_ids=None, universe={"min_avg_amount": 1e15, "min_observations": 3}))
        opt.execute_job(q["job_id"])
        v = opt.get_job(q["job_id"])
        self.assertEqual(v["status"], "failed")
        self.assertIn("universe is empty", v["error"])


class ApiTests(JobCase):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.client = TestClient(app)

    def test_full_api_lifecycle(self):
        cat = self.client.get("/api/strategy-lab/optimization/search-spaces").json()
        self.assertEqual(set(cat["templates"]), set(TEMPLATES))
        r = self.client.post("/api/strategy-lab/optimization/jobs", json=self.payload())
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()["job_id"]  # TestClient runs the BackgroundTask before returning
        status = self.client.get(f"/api/strategy-lab/optimization/jobs/{job}").json()
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["progress"]["completed"], 8)
        base = f"/api/strategy-lab/optimization/jobs/{job}"
        results = self.client.get(base + "/results?objective=calmar&limit=5").json()
        self.assertEqual(len(results["rows"]), 5)
        surface = self.client.get(base + "/surface?x=selection.top_n&y=momentum.lookback").json()
        self.assertTrue(surface["cells"])
        rob = self.client.get(base + "/robustness?top_fraction=0.5").json()
        self.assertEqual(rob["analysis"]["rule"]["top_fraction"], .5)
        for body in (cat, status, results, surface, rob):
            _no_key(body)
        self.assertEqual(len(self.client.get("/api/strategy-lab/optimization/jobs").json()), 1)
        self.assertEqual(self.client.post(base + "/cancel").status_code, 409)
        self.assertEqual(self.client.post(base + "/resume").status_code, 409)

    def test_api_errors(self):
        c = self.client
        self.assertEqual(c.post("/api/strategy-lab/optimization/jobs",
                                json={"template_key": "nope"}).status_code, 400)
        self.assertEqual(c.get("/api/strategy-lab/optimization/jobs/missing").status_code, 404)
        self.assertEqual(c.get("/api/strategy-lab/optimization/jobs/missing/results").status_code, 404)
        job = c.post("/api/strategy-lab/optimization/jobs", json=self.payload()).json()["job_id"]
        self.assertEqual(c.get(f"/api/strategy-lab/optimization/jobs/{job}/results?objective=x").status_code, 400)
        self.assertEqual(c.get(f"/api/strategy-lab/optimization/jobs/{job}/surface?x=zz").status_code, 400)


if __name__ == "__main__":
    unittest.main()
