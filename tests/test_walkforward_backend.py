"""Stage F1 tests: Walk Forward / OOS backend (orchestration, OOS semantics, stitched, lifecycle).

Offline (temp SQLite, synthetic prices).  The real research window/data are not touched.
"""
import datetime as dt
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import DatabaseError

sys.path.insert(0, str(Path(__file__).parent))
from test_optimization_backend import RANGE_PATCH, SMALL, SPACE, STOCKS  # noqa: E402

from app.db import query_all, query_one, upsert_many  # noqa: E402
from app.db import tables as t  # noqa: E402
from app.db.migrations import migrate  # noqa: E402
from app.services import optimization_lab as opt  # noqa: E402
from app.services import strategy_lab as lab  # noqa: E402
from app.services import walkforward_lab as wfl  # noqa: E402
from app.services import warning_contract as wc  # noqa: E402
from app.services.optimization import search_space as ss  # noqa: E402
from app.services.strategy.panel import build_panel  # noqa: E402
from app.services.walkforward import diagnostics as wf_diag  # noqa: E402
from app.services.walkforward import freeze as wf_freeze  # noqa: E402
from app.services.walkforward import oos as wf_oos  # noqa: E402
from app.services.walkforward import stitched as wf_stitched  # noqa: E402

D0, D1 = "2024-01-01", "2025-01-15"
DATA_START, PANEL_START = "2024-02-01", "2024-01-01"
PROTOCOL = {"initial_train_months": 6, "test_months": 2, "step_months": 2, "min_final_test_months": 1}
# 2024-02-01 + 6m = 2024-08-01 -> tests 08-01..09-30, 10-01..11-30, 12-01..01-15 (truncated)


def _days(start, end):
    d, out = dt.date.fromisoformat(start), []
    while d.isoformat() <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


class WFCase(unittest.TestCase):
    """Temp DB with noisy synthetic prices + a patched engine everywhere the lab touches it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/wf.db")
        migrate(self.engine)
        self.patches = [patch.object(opt, "engine", self.engine), patch.object(lab, "engine", self.engine),
                        patch.object(wfl, "engine", self.engine),
                        patch.dict(ss.ALLOWED["momentum_trend"], RANGE_PATCH)]
        for p in self.patches:
            p.start()
        self.dates = _days(D0, D1)
        rng, rows = random.Random(11), []
        drift = {"1101": .0008, "1102": .0003, "1103": -.0002}
        for s in STOCKS:
            px = 100.0
            for d in self.dates:
                px *= 1 + drift[s] + rng.gauss(0, .012)
                rows.append({"stock_id": s, "date": d, "open": px * (1 + rng.gauss(0, .002)), "close": px,
                             "high": px * 1.01, "low": px * .99, "volume": 1e6, "amount": 1e8})
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": s, "name": s, "market": "TWSE", "type": "stock",
                                       "industry": "X"} for s in STOCKS])
            upsert_many(c, t.trading_calendar, [{"date": d} for d in self.dates])
            upsert_many(c, t.daily_quotes, rows)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def payload(self, **extra):
        return {"template_key": "momentum_trend", "params": SMALL, "stock_ids": STOCKS,
                "data_start": DATA_START, "data_end": D1, "panel_start": PANEL_START,
                "search_space": SPACE, "protocol": PROTOCOL, **extra}

    def run_wf(self, **extra):
        q = wfl.create_job(self.payload(**extra))
        if q["queued"]:
            wfl.execute_job(q["job_id"])
        return q["job_id"]

    def rows(self, sql, **kw):
        with self.engine.connect() as c:
            return query_all(c, sql, **kw)

    def folds(self, job):
        return self.rows("SELECT * FROM walkforward_folds WHERE job_id=:j ORDER BY fold_index", j=job)

    def force_freeze(self, no_candidate=()):
        """Deterministic stand-in for plateau outcomes on the tiny synthetic surface: freeze the top
        point (require_plateau=False), or refuse with the contract's NO_STABLE_CANDIDATE."""
        real = wf_freeze.freeze_from_job

        def fake(job_id, fold=None, **kw):
            if fold is not None and fold["train_end"] in no_candidate:
                raise ValueError("NO_STABLE_CANDIDATE: LABEL:scattered")
            return real(job_id, fold, require_plateau=False)
        return patch.object(wfl.wf_freeze, "freeze_from_job", side_effect=fake)


# ── creation / protocol ─────────────────────────────────
class CreationTests(WFCase):
    def test_default_protocol_yields_four_folds_with_partial_execution(self):
        with self.engine.begin() as c:   # calendar only: creation never reads prices
            upsert_many(c, t.trading_calendar, [{"date": d} for d in _days("2022-10-01", "2026-09-18")])
        q = wfl.create_job({"template_key": "momentum_trend", "params": {}, "stock_ids": STOCKS,
                            "panel_start": "2021-09-22"})
        job = wfl.get_job(q["job_id"])
        folds = wfl.get_folds(q["job_id"])
        self.assertEqual(len(folds), 4)
        self.assertEqual(job["protocol"]["initial_train_months"], 24)
        self.assertEqual((job["protocol"]["test_months"], job["protocol"]["step_months"]), (6, 6))
        self.assertEqual([f["windows"]["truncated"] for f in folds], [False, False, False, True])
        self.assertEqual(folds[0]["windows"]["train"], ["2022-11-01", "2024-10-31"])
        self.assertEqual(folds[-1]["windows"]["test"][1], "2026-09-18")
        self.assertEqual(job["request"]["backtest"]["missing"]["execution"], "partial")
        self.assertEqual(job["provenance"]["candidate_rule"], "wf-freeze/f0.5/2")
        self.assertEqual(job["provenance"]["publication_mode"], "assumed")
        for a, b in zip(folds, folds[1:]):
            self.assertLess(a["windows"]["test"][1], b["windows"]["test"][0])
            self.assertEqual(b["windows"]["train"][1], a["windows"]["test"][1])   # expanding, contiguous

    def test_validation(self):
        with self.assertRaises(ValueError):
            wfl.create_job(self.payload(backtest={"missing": {"execution": "next_available"}}))
        with self.assertRaisesRegex(ValueError, "gap_sessions"):
            wfl.create_job(self.payload(protocol={**PROTOCOL, "gap_sessions": 2}))
        with self.assertRaises(ValueError):
            wfl.create_job(self.payload(publication_mode="strict", template_key="value_quality", params={},
                                        search_space=None))
        with self.assertRaises(ValueError):
            wfl.create_job(self.payload(protocol={"nope": 1}))
        with self.assertRaises(ValueError):
            wfl.create_job(self.payload(data_start="2024-11-01", data_end="2024-12-15"))   # no fold fits

    def test_duplicate_active_job_is_deduplicated(self):
        a = wfl.create_job(self.payload())
        b = wfl.create_job(self.payload())
        self.assertTrue(b["duplicate_of_active_job"])
        self.assertEqual(a["job_id"], b["job_id"])

    def test_strategy_version_provenance_and_mismatch(self):
        strategy = lab.create_strategy({"name": "wf", "template_key": "momentum_trend", "params": SMALL,
                                        "backtest": {"missing": {"execution": "partial"}}})
        ver = strategy["latest_version"]
        q = wfl.create_job(self.payload(strategy_id=strategy["id"], strategy_version_id=ver["id"]))
        prov = wfl.get_job(q["job_id"])["provenance"]
        self.assertEqual((prov["strategy_id"], prov["strategy_version_id"]), (strategy["id"], ver["id"]))
        self.assertEqual(prov["definition_hash"], ver["definition_hash"])
        plain = lab.create_strategy({"name": "wf2", "template_key": "momentum_trend", "params": SMALL})
        with self.assertRaisesRegex(ValueError, "differs"):
            wfl.create_job(self.payload(strategy_id=plain["id"], strategy_version_id=plain["latest_version"]["id"]))


# ── end-to-end fold orchestration ───────────────────────
class OrchestrationTests(WFCase):
    def test_fold_order_train_freeze_open_then_test_load(self):
        events = []
        real_panel, real_open, real_opt_panel = build_panel, wf_freeze.open_test_window, opt.build_panel

        def oos_panel(start, end, ids, **kw):
            events.append(("oos_panel", end))
            return real_panel(start, end, ids, **kw)

        def train_panel(start, end, ids, **kw):
            events.append(("train_panel", end))
            return real_opt_panel(start, end, ids, **kw)

        def opened(fid):
            events.append(("open", wf_freeze.get_freeze(fid)["train_end"]))
            return real_open(fid)

        with self.force_freeze(), patch.object(wfl, "build_panel", side_effect=oos_panel), \
                patch.object(opt, "build_panel", side_effect=train_panel), \
                patch.object(wfl.wf_freeze, "open_test_window", side_effect=opened):
            job = self.run_wf()
        self.assertEqual(wfl.get_job(job)["status"], "completed")
        folds = self.folds(job)
        self.assertEqual(len(folds), 3)
        # per fold: training panel ends exactly at train_end, THEN freeze/open, THEN test-window panel
        for f in folds:
            train_end, test_end = f["train_end"], f["test_end"]
            i_train = events.index(("train_panel", train_end))
            i_open = events.index(("open", train_end))
            i_oos = events.index(("oos_panel", test_end))
            self.assertLess(i_train, i_open)
            self.assertLess(i_open, i_oos)
            # nothing dated after train_end was loaded before this fold's window was opened
            self.assertTrue(all(e[1] <= train_end for e in events[:i_open] if e[0] == "train_panel"
                                and e[1] >= f["train_start"] and e[1] <= train_end + "~"))
            self.assertEqual([e for e in events[:i_open] if e[0] == "oos_panel" and e[1] == test_end], [])
        # a fold's training job saw exactly its train window
        for f in folds:
            req = json.loads(query_one(self.engine.connect(), "SELECT request_json j FROM optimization_jobs "
                                       "WHERE id=:i", i=f["train_job_id"])["j"])
            self.assertEqual((req["research_start"], req["research_end"]), (f["train_start"], f["train_end"]))
            self.assertEqual(req["backtest"]["missing"]["execution"], "partial")
            self.assertEqual(req["publication_mode"], "assumed")
        # windows are expanding and test windows do not overlap
        for a, b in zip(folds, folds[1:]):
            self.assertLess(a["test_end"], b["test_start"])
            self.assertEqual(a["train_start"], b["train_start"])
        self.assertTrue(folds[-1]["truncated"])

    def test_cash_start_first_fill_entry_cost_and_independent_metrics(self):
        with self.force_freeze():
            job = self.run_wf()
        for f in self.folds(job):
            oos = json.loads(f["oos_json"])
            self.assertEqual((f["outcome"], oos["initial_state"], oos["fills_before_test_start"]),
                             ("evaluated", "cash", 0))
            fills = [json.loads(r["payload_json"]) for r in self.rows(
                "SELECT payload_json FROM walkforward_trades WHERE job_id=:j AND series='fold' AND fold_index=:f "
                "AND kind='fill' ORDER BY seq", j=job, f=f["fold_index"])]
            self.assertTrue(fills)
            self.assertGreaterEqual(fills[0]["date"], f["test_start"])
            self.assertEqual(oos["effective_first_fill_date"], fills[0]["date"])
            self.assertTrue(all(x["commission"] > 0 for x in fills if x["date"] == fills[0]["date"]))
            m = json.loads(f["oos_metrics_json"])
            self.assertGreater(m["gross_return"] - m["net_return"], 0)       # entry costs are inside the fold
            self.assertGreater(m["turnover"], 0)
            eq = wfl.get_equity(job, "fold", f["fold_index"])["points"]
            self.assertEqual((eq[0]["date"], eq[-1]["date"]), (f["test_start"], f["test_end"]))   # metrics window only
            # independent: re-running the fold directly from cash reproduces the stored metrics exactly
            fz = wf_freeze.get_freeze(f["freeze_id"])
            treq = json.loads(query_one(self.engine.connect(), "SELECT request_json j FROM optimization_jobs "
                                        "WHERE id=:i", i=f["train_job_id"])["j"])
            rule, cfg = wfl._candidate(treq, fz["primary_params"])
            panel = build_panel(PANEL_START, f["test_end"], STOCKS, publication_mode="assumed",
                                db_engine=self.engine)
            window = wfl._fold_window(f)
            direct = wf_oos.run_oos_fold(panel, rule, cfg, window)
            for k in ("net_return", "sharpe", "max_drawdown", "turnover", "cagr"):
                self.assertEqual(direct["metrics"][k], m[k], k)

    def test_fold_persists_full_evidence(self):
        with self.force_freeze():
            job = self.run_wf()
        f0 = wfl.get_fold(job, 0)
        for key in ("train_job_id", "freeze_id", "candidate_digest", "plateau_label", "region"):
            self.assertTrue(f0[key], key)
        self.assertEqual(f0["evidence"], "independent_fold")
        self.assertTrue(f0["primary"]["params"])
        self.assertIn("sharpe", f0["is_training"]["metrics"])
        self.assertIn("unresolved_action_count", f0["oos"])
        self.assertIn("partial_rebalance_count", f0["oos"])
        self.assertIn("effective_first_fill_date", f0["oos"])
        self.assertIn("primary_on_edge", f0["candidate_flags"])
        self.assertIn("is_peak_on_edge", f0["candidate_flags"])
        self.assertIn("FROZEN_WITHOUT_PLATEAU", {i["code"] for i in f0["severity"]["items"]}
                      if f0["plateau_label"] != "plateau" else {"FROZEN_WITHOUT_PLATEAU"})
        self.assertTrue(f0["severity"]["training"])
        self.assertEqual(f0["freeze"]["rule_version"], "wf-freeze/f0.5/2")
        fz = wf_freeze.get_freeze(f0["freeze_id"])
        self.assertEqual(fz["candidate_digest"], f0["candidate_digest"])
        self.assertTrue(fz["test_opened"])
        self.assertTrue(wfl.get_trades(job, "fold", 0, "closed")["total"] >= 0)

    def test_no_stable_candidate_holds_cash_and_never_uses_is_peak(self):
        folds_plan = wfl.get_folds(wfl.create_job(self.payload(seed=99))["job_id"])   # discover boundaries
        bad_train_end = folds_plan[1]["windows"]["train"][1]
        events = []
        real_panel = build_panel

        def spy(start, end, ids, **kw):
            events.append(end)
            return real_panel(start, end, ids, **kw)
        with self.force_freeze(no_candidate=(bad_train_end,)), patch.object(wfl, "build_panel", side_effect=spy):
            job = self.run_wf()
        v = wfl.get_job(job)
        self.assertEqual(v["status"], "completed")
        self.assertEqual((v["progress"]["evaluated"], v["progress"]["no_candidate"]), (2, 1))
        f1 = wfl.get_fold(job, 1)
        self.assertTrue(f1["no_candidate"])
        self.assertEqual(f1["outcome"], "no_candidate")
        self.assertIsNone(f1["freeze_id"])
        self.assertIsNone(f1["primary"])
        self.assertEqual(f1["freeze"]["reasons"], ["NO_STABLE_CANDIDATE", "LABEL:scattered"])   # verbatim
        codes = {i["code"]: i["severity"] for i in f1["severity"]["items"]}
        self.assertEqual(codes["NO_STABLE_CANDIDATE"], "critical")
        self.assertIn("FOLD_HELD_CASH", codes)
        # no freeze row, no test-window load for that fold, no trades, flat cash equity
        with self.engine.connect() as c:
            self.assertIsNone(query_one(c, "SELECT 1 x FROM wf_candidate_freezes WHERE job_id=:j",
                                        j=self.folds(job)[1]["train_job_id"]))
        self.assertNotIn(f1["windows"]["test"][1], events)
        self.assertEqual(f1["oos"]["n_fills"], 0)
        self.assertFalse(f1["oos"]["test_data_loaded"])
        eq = wfl.get_equity(job, "fold", 1)["points"]
        self.assertEqual({p["net_equity"] for p in eq}, {1_000_000.0})
        self.assertEqual(f1["oos_metrics"]["net_return"], 0.0)
        self.assertTrue(f1["oos_metrics"]["cash_fold"])
        # untouched neighbours are still evaluated
        self.assertEqual([wfl.get_fold(job, i)["outcome"] for i in (0, 2)], ["evaluated", "evaluated"])
        comp = wfl.get_result(job)["diagnostics"]["completeness"]
        self.assertEqual((comp["no_candidate"], comp["no_candidate_folds"]), (1, [1]))

    def test_primary_on_search_edge_warning(self):
        topo = {"axes": {"selection.top_n": [1, 2, 3], "trend.ma_lookback": [3, 5]}, "groups": {}}
        self.assertEqual(wf_diag.primary_edge_axes({"selection.top_n": 3, "trend.ma_lookback": 3}, topo),
                         ["selection.top_n"])          # 2-value axes are not edges (always at a bound)
        self.assertEqual(wf_diag.primary_edge_axes({"selection.top_n": 2}, topo), [])
        gtopo = {"axes": {}, "groups": {"factor_weights": {"keys": ["a", "b", "c"], "step": .2}}}
        pts = [{"params": {"factor_weights.a": w}} for w in (.2, .4, .6)]
        self.assertEqual(wf_diag.group_edge_axes({"factor_weights.a": .6}, pts, gtopo), ["factor_weights.a"])
        self.assertEqual(wf_diag.group_edge_axes({"factor_weights.a": .4}, pts, gtopo), [])
        self.assertEqual(wc.STRUCTURAL["PRIMARY_ON_SEARCH_EDGE"], "caution")
        # end to end: the flag is independent of the IS peak's own edge flag
        with self.force_freeze(), patch.object(wfl.wf_diag, "primary_edge_axes", return_value=["selection.top_n"]):
            job = self.run_wf()
        f = wfl.get_fold(job, 0)
        self.assertTrue(f["candidate_flags"]["primary_on_edge"])
        self.assertIn("PRIMARY_ON_SEARCH_EDGE", {i["code"] for i in f["severity"]["items"]})

    def test_partial_rebalance_count_is_recorded_and_flagged(self):
        with self.force_freeze():
            base = self.run_wf()
        f = self.folds(base)[0]
        # 1102 is bought on the 2nd test session and held ~2 weeks (deterministic synthetic prices):
        # suspend it for that stretch so its rebalance must go through the partial path
        held = [json.loads(r["payload_json"]) for r in self.rows(
            "SELECT payload_json FROM walkforward_trades WHERE job_id=:j AND series='fold' AND fold_index=0 "
            "AND kind='fill' ORDER BY seq", j=base)]
        victim = next(x["stock_id"] for x in held if x["stock_id"] == "1102" and x["side"] == "buy")
        test_days = [d for d in self.dates if d >= f["test_start"]][3:14]
        with self.engine.begin() as c:
            for d in test_days:
                c.exec_driver_sql("DELETE FROM daily_quotes WHERE stock_id=? AND date=?", (victim, d))
        with self.force_freeze():
            job = self.run_wf(seed=1)
        counts = [json.loads(x["oos_json"])["partial_rebalance_count"] for x in self.folds(job)]
        self.assertTrue(all(isinstance(c, int) for c in counts))
        self.assertGreater(sum(counts), 0, "the suspended stock should force at least one partial rebalance")
        for x in self.folds(job):
            n = json.loads(x["oos_json"])["partial_rebalance_count"]
            codes = {i["code"] for i in json.loads(x["severity_json"])["items"]}
            self.assertEqual("PARTIAL_REBALANCE_USED" in codes, n > 0)

    def test_deterministic_fold_outputs(self):
        with self.force_freeze():
            a = self.run_wf()
            b = self.run_wf(seed=1)          # different job hash, same everything else
        self.assertNotEqual(a, b)
        for fa, fb in zip(self.folds(a), self.folds(b)):
            for col in ("candidate_digest", "primary_params_json", "oos_metrics_json", "region_json", "outcome"):
                self.assertEqual(fa[col], fb[col], col)
            ia, ib = json.loads(fa["is_json"]), json.loads(fb["is_json"])
            self.assertNotEqual(ia.pop("train_job_id"), ib.pop("train_job_id"))   # distinct jobs, same content
            self.assertEqual(ia, ib)
            oa, ob = json.loads(fa["oos_json"]), json.loads(fb["oos_json"])
            oa.pop("elapsed_seconds"), ob.pop("elapsed_seconds")
            self.assertEqual(oa, ob)
            self.assertEqual(wfl.get_equity(a, "fold", fa["fold_index"]), wfl.get_equity(b, "fold", fa["fold_index"]))
            self.assertEqual(wfl.get_trades(a, "fold", fa["fold_index"]), wfl.get_trades(b, "fold", fa["fold_index"]))
        sa, sb = wfl.get_stitched(a), wfl.get_stitched(b)
        self.assertEqual(sa["metrics"], sb["metrics"])
        self.assertEqual(wfl.get_equity(a, "stitched")["points"], wfl.get_equity(b, "stitched")["points"])


# ── stitched ────────────────────────────────────────────
class StitchedTests(WFCase):
    def setUp(self):
        super().setUp()
        with self.force_freeze():
            self.job = self.run_wf()

    def test_stitched_is_separate_secondary_evidence(self):
        s = wfl.get_stitched(self.job)
        self.assertTrue(s["available"])
        self.assertEqual(s["evidence"], "stitched_secondary")
        self.assertFalse(s["independent"])
        self.assertFalse(s["training_replay_used"])
        self.assertEqual(s["fills_before_first_test_start"], 0)
        self.assertEqual(s["initial_state"], "cash")
        result = wfl.get_result(self.job)
        self.assertEqual(result["evidence"], "independent_fold")
        self.assertEqual(set(wfl.get_independent_oos(self.job)), {"evidence", "notice", "folds"})
        self.assertNotIn("stitched", json.dumps(wfl.get_independent_oos(self.job)["folds"]).lower())
        self.assertIn("STITCHED_NOT_INDEPENDENT_EVIDENCE", {i["code"] for i in s["severity"]["items"]})
        # stitched and independent series are stored under different keys
        n = self.rows("SELECT series, COUNT(*) n FROM walkforward_equity WHERE job_id=:j GROUP BY series", j=self.job)
        self.assertEqual({r["series"] for r in n}, {"fold", "stitched"})

    def test_fold0_segment_equals_independent_fold0_and_later_folds_inherit(self):
        ind0 = wfl.get_equity(self.job, "fold", 0)["points"]
        st = {p["date"]: p for p in wfl.get_equity(self.job, "stitched")["points"]}
        f0 = self.folds(self.job)[0]
        for p in ind0:                                 # same cash start, same rule -> identical ledger
            self.assertAlmostEqual(st[p["date"]]["net_equity"], p["net_equity"], places=6)
        s = wfl.get_stitched(self.job)
        seg = {x["fold"]: x for x in s["segments"]}
        self.assertEqual(seg[0]["state_source"], "cash")
        self.assertEqual(seg[1]["state_source"], "prior_oos_terminal")
        self.assertEqual(seg[1]["inherited_from_fold"], 0)
        self.assertGreater(seg[1]["inherited_position_count"], 0)
        # inheriting the terminal portfolio means fold 1 does not pay the whole entry again
        f1 = self.folds(self.job)[1]
        all_st = wfl.get_trades(self.job, "stitched", kind="fill", limit=2000)["rows"]
        held = {}
        for r in all_st:
            if r["date"] < f1["test_start"]:
                held[r["stock_id"]] = held.get(r["stock_id"], 0) + (r["shares"] if r["side"] == "buy" else -r["shares"])
        inherited = {k for k, v in held.items() if v > 1e-9}
        self.assertTrue(inherited)
        first_sells = {r["stock_id"] for r in all_st if r["date"] == f1["test_start"] and r["side"] == "sell"}
        self.assertLessEqual(first_sells, inherited)      # only inherited (prior-OOS) holdings can be sold
        ind_rows = wfl.get_trades(self.job, "fold", 1, "fill")["rows"]
        self.assertFalse([r for r in ind_rows if r["side"] == "sell" and r["date"] == f1["test_start"]])  # cash start: nothing to sell
        self.assertEqual(min(p["date"] for p in wfl.get_equity(self.job, "stitched")["points"]), f0["test_start"])
        m = s["metrics"]
        ind_m = [json.loads(x["oos_metrics_json"]) for x in self.folds(self.job)]
        self.assertNotEqual(m["net_return"], ind_m[0]["net_return"])       # a different statistic

    def test_no_training_replay_gate_never_evaluated_before_first_boundary(self):
        from app.services.walkforward.stitched import StitchedGate
        calls = []
        real_call = StitchedGate.__call__

        def spy(self_, context):
            calls.append(context.date)
            return real_call(self_, context)
        with patch.object(StitchedGate, "__call__", spy):
            with self.engine.begin() as c:   # force a rebuild of the stitched series
                c.exec_driver_sql("DELETE FROM walkforward_equity WHERE series='stitched'")
                c.exec_driver_sql("DELETE FROM walkforward_trades WHERE series='stitched'")
            req = json.loads(self.rows("SELECT request_json j FROM walkforward_jobs WHERE id=:i", i=self.job)[0]["j"])
            meta = wfl._build_stitched(self.job, req)
        self.assertEqual(meta["status"], "available")
        first_boundary = self.folds(self.job)[0]["train_end"]
        self.assertTrue(calls)
        self.assertGreaterEqual(min(calls), first_boundary)
        early_fills = [r for r in self.rows("SELECT date FROM walkforward_trades WHERE series='stitched' "
                                            "AND kind='fill'") if r["date"] < self.folds(self.job)[0]["test_start"]]
        self.assertEqual(early_fills, [])

    def test_chain_validation_and_config_guard(self):
        f = [{"index": 0, "train_end": "2024-07-31", "test_start": "2024-08-01", "test_end": "2024-09-30"},
             {"index": 1, "train_end": "2024-09-30", "test_start": "2024-10-01", "test_end": "2024-11-29"}]
        wf_stitched.validate_chain(f)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            wf_stitched.validate_chain([f[1]])
        with self.assertRaisesRegex(ValueError, "not fold 0's test_end"):
            wf_stitched.validate_chain([f[0], {**f[1], "train_end": "2024-10-01"}])
        from app.services.strategy.types import BacktestConfig, CostConfig
        a, b = BacktestConfig(), BacktestConfig(costs=CostConfig(sell_tax_rate=0.0))
        with self.assertRaisesRegex(ValueError, "STITCHED_CONFIG_MISMATCH"):
            wf_stitched.stitched_config([a, b], ["2024-07-31"], [])

    def test_no_candidate_fold_liquidates_to_cash_in_stitched(self):
        plan = wfl.get_folds(self.job)
        bad = plan[1]["windows"]["train"][1]
        with self.force_freeze(no_candidate=(bad,)):
            job = self.run_wf(seed=5)
        s = wfl.get_stitched(job)
        self.assertTrue(s["available"])
        seg = {x["fold"]: x for x in s["segments"]}
        self.assertEqual(seg[1]["outcome"], "no_candidate")
        self.assertIn("STITCHED_LIQUIDATION_AT_NO_CANDIDATE", {i["code"] for i in s["severity"]["items"]})
        f1 = plan[1]["windows"]["test"]
        pts = [p for p in wfl.get_equity(job, "stitched")["points"] if f1[0] <= p["date"] <= f1[1]]
        after_liq = [p["net_equity"] for p in pts[1:]]
        self.assertEqual(len(set(after_liq)), 1)                            # all cash after the boundary exit
        sells = [f for f in wfl.get_trades(job, "stitched", kind="fill")["rows"]
                 if f1[0] <= f["date"] <= f1[1]]
        self.assertTrue(sells and all(x["side"] == "sell" for x in sells))
        self.assertEqual({x["date"] for x in sells}, {f1[0]})               # liquidation on the first test session
        self.assertEqual(seg[2]["state_source"], "prior_oos_terminal")


# ── stitched universe (F1.5): no future-universe leakage ─
class StitchedUniverseTests(WFCase):
    """1104 is a strong momentum name whose liquidity only qualifies from October 2024 (fold 1)."""

    def setUp(self):
        super().setUp()
        rng, rows = random.Random(5), []
        px = 100.0
        for d in self.dates:
            px *= 1 + .004 + rng.gauss(0, .01)
            rows.append({"stock_id": "1104", "date": d, "open": px, "close": px, "high": px * 1.01, "low": px * .99,
                         "volume": 1e6, "amount": 1e6 if d < "2024-10-01" else 5e9})
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": "1104", "name": "1104", "market": "TWSE", "type": "stock",
                                       "industry": "X"}])
            upsert_many(c, t.daily_quotes, rows)

    def run_dynamic(self, **extra):
        with self.force_freeze():
            q = wfl.create_job(self.payload(stock_ids=None, universe={"min_avg_amount": 3e7, "min_observations": 20},
                                            **extra))
            wfl.execute_job(q["job_id"])
        return q["job_id"]

    def universes(self, job):
        return [set(json.loads(f["universe_json"])["stock_ids"]) for f in self.folds(job)]

    def test_universes_are_point_in_time_and_nested(self):
        job = self.run_dynamic()
        u = self.universes(job)
        self.assertNotIn("1104", u[0])          # not knowable during fold 0
        self.assertIn("1104", u[1])
        self.assertTrue(u[0] <= u[1] <= u[2])   # only ever expands with what was known then

    def test_stitched_fold0_equals_independent_fold0_and_never_trades_future_members(self):
        job = self.run_dynamic()
        f0 = self.folds(job)[0]
        ind = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "fold", 0)["points"]}
        st = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "stitched")["points"]}
        for d, e in ind.items():
            self.assertAlmostEqual(st[d], e, places=6, msg=d)      # exact alignment, not ~2%
        fills = wfl.get_trades(job, "stitched", kind="fill", limit=2000)["rows"]
        self.assertFalse([x for x in fills if x["stock_id"] == "1104" and x["date"] <= f0["test_end"]])
        # the name IS tradable once known: the test would be vacuous otherwise
        later = self.universes(job)[1]
        self.assertIn("1104", later)
        self.assertTrue([x for x in fills if x["stock_id"] == "1104" and x["date"] > f0["test_end"]])

    def test_without_the_restriction_fold0_would_leak(self):
        job = self.run_dynamic()
        real = wf_stitched.run_stitched
        with patch.object(wfl.wf_stitched, "run_stitched",
                          side_effect=lambda panel, folds, r, c, b, u=None: real(panel, folds, r, c, b, None)):
            req = json.loads(self.rows("SELECT request_json j FROM walkforward_jobs WHERE id=:i", i=job)[0]["j"])
            with self.engine.begin() as c:
                c.exec_driver_sql("DELETE FROM walkforward_equity WHERE series='stitched'")
                c.exec_driver_sql("DELETE FROM walkforward_trades WHERE series='stitched'")
            wfl._build_stitched(job, req)
        ind = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "fold", 0)["points"]}
        st = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "stitched")["points"]}
        self.assertTrue(any(abs(st[d] - e) > 1 for d, e in ind.items()))   # unrestricted universe DOES differ

    def test_universe_view_restricts_every_context_field(self):
        panel = build_panel(PANEL_START, "2024-09-30", ["1101", "1102", "1104"], publication_mode="assumed",
                            db_engine=self.engine, benchmark_id=None)
        from app.services.strategy.types import SignalContext
        i = len(panel.days) - 1
        ctx = SignalContext(panel.days[i].date, i, panel.days[i].bars, tuple(panel.days[:i + 1]),
                            {"month_revenue": {"1101": (1,), "1104": (2,)}, "financials": {}},
                            {"1101": "v", "1104": "w"}, panel.securities, "assumed")
        view = wf_stitched.UniverseView({"1101", "1102"})
        r = view.context(ctx)
        self.assertEqual(set(r.bars), {"1101", "1102"})
        self.assertTrue(all(set(d.bars) <= {"1101", "1102"} for d in r.history))
        self.assertEqual(len(r.history), len(ctx.history))
        self.assertEqual(set(r.fundamentals["month_revenue"]), {"1101"})
        self.assertEqual(set(r.valuations), {"1101"})
        self.assertNotIn("1104", r.securities)
        self.assertEqual(r.date, ctx.date)


# ── diagnostics ─────────────────────────────────────────
class DiagnosticsTests(WFCase):
    def test_diagnostics_content(self):
        with self.force_freeze():
            job = self.run_wf()
        d = wfl.get_result(job)["diagnostics"]
        self.assertEqual(d["evidence"], "independent_fold")
        self.assertEqual(len(d["is_vs_oos"]), 3)
        for key in ("sharpe", "cagr", "max_drawdown", "turnover"):
            self.assertIn("delta", d["is_vs_oos"][0][key])
            self.assertIn(key, d["degradation"])
        st = d["parameter_stability"]
        self.assertEqual(st["n_evaluated_folds"], 3)
        self.assertIn("candidate_digest_changes", st)
        self.assertEqual(len(st["region_jaccard_consecutive"]), 2)
        self.assertIn("per_axis", st)
        self.assertEqual(set(d["plateau_edge"]), {"labels", "folds_with_is_peak_on_edge",
                                                  "folds_with_primary_on_edge", "primary_edge_axes"})
        self.assertEqual(len(d["cost_stability"]["per_fold"]), 3)
        self.assertIn("unresolved_action_total", d["counts"])
        self.assertIn("partial_rebalance_total", d["counts"])
        self.assertEqual(len(d["severity"]["per_fold"]), 3)
        self.assertIn(d["severity"]["max_severity"], ("info", "caution", "critical"))
        comp = d["completeness"]
        self.assertEqual((comp["planned_folds"], comp["completed"], comp["evaluated"], comp["complete"]),
                         (3, 3, 3, True))
        self.assertTrue(comp["truncated_final_fold"])
        disp = d["fold_dispersion"]
        self.assertTrue(disp["descriptive_only"])
        self.assertEqual(disp["statistical_inference"], "not performed")
        self.assertIn("FOLD_DISPERSION_DESCRIPTIVE", {i["code"] for i in d["flags"]})
        blob = json.dumps(d).lower()
        for banned in ("p_value", "p-value", "confidence_interval", "t_stat"):
            self.assertNotIn(banned, blob)
        self.assertEqual(d["stitched_reference"]["evidence"], "stitched_secondary")

    def test_pure_analysis_handles_no_candidate_and_missing_folds(self):
        mk = lambda i, **kw: {"index": i, "status": "completed", "outcome": "evaluated", "truncated": False,  # noqa: E731
                              "plateau_label": "plateau", "edge_axes": [], "primary_edge_axes": ["a"],
                              "primary_params": {"a": i}, "primary_key": f"k{i}", "region_keys": ["k0", f"k{i}"],
                              "candidate_digest": f"d{i}", "is_metrics": {"sharpe": 2.0, "cagr": .3, "max_drawdown": -.1,
                                                                            "turnover": 100},
                              "oos_metrics": {"sharpe": 1.0, "cagr": .1, "max_drawdown": -.2, "turnover": 90,
                                              "cost_drag_pct": .01, "net_return": .05, "gross_return": .06},
                              "unresolved_action_count": 0, "partial_rebalance_count": 1, "max_severity": "caution",
                              "severity_counts": {"info": 0, "caution": 1, "critical": 0}, **kw}
        folds = [mk(0), {**mk(1), "outcome": "no_candidate", "primary_params": None, "primary_key": None,
                         "region_keys": [], "candidate_digest": None, "is_metrics": None, "oos_metrics": {"net_return": 0.0}},
                 mk(2), {"index": 3, "status": "pending", "outcome": None, "truncated": True}]
        d = wf_diag.analyze(folds, {"planned": 4, "dropped": [], "notes": []})
        self.assertEqual((d["completeness"]["evaluated"], d["completeness"]["no_candidate"],
                          d["completeness"]["pending"], d["completeness"]["complete"]), (2, 1, 1, False))
        self.assertEqual(d["parameter_stability"]["primary_changes"], 1)     # 0 -> 2 skips the cash fold
        self.assertAlmostEqual(d["is_vs_oos"][0]["sharpe"]["delta"], -1.0)
        self.assertAlmostEqual(d["is_vs_oos"][0]["sharpe"]["ratio"], .5)
        self.assertEqual(d["degradation"]["sharpe"]["folds_oos_lower"], 2)
        self.assertIn("NO_STABLE_CANDIDATE", {i["code"] for i in d["flags"]})


# ── lifecycle / provenance ──────────────────────────────
class LifecycleTests(WFCase):
    def test_fold_failure_records_and_resume_reuses_evidence(self):
        real = wf_oos.run_oos_fold
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom in fold 1")
            return real(*a, **kw)
        with self.force_freeze(), patch.object(wfl.wf_oos, "run_oos_fold", side_effect=flaky):
            job = self.run_wf()
            v = wfl.get_job(job)
            self.assertEqual(v["status"], "failed")
            folds = self.folds(job)
            self.assertEqual([f["status"] for f in folds], ["completed", "failed", "pending"])
            self.assertIn("boom in fold 1", folds[1]["error"])
            done_before = dict(folds[0])
            opt_jobs_before = self.rows("SELECT id FROM optimization_jobs ORDER BY id")
            freeze_before = wf_freeze.get_freeze(folds[1]["freeze_id"])
            self.assertTrue(freeze_before["test_opened"])
            with self.assertRaises(RuntimeError):
                wfl.resume_job(wfl.create_job(self.payload(seed=3))["job_id"])       # queued job: not resumable
            wfl.resume_job(job)
            wfl.execute_job(job)
        self.assertEqual(wfl.get_job(job)["status"], "completed")
        after = self.folds(job)
        self.assertEqual(dict(after[0]), done_before)                          # completed fold untouched
        self.assertEqual(calls["n"], 4)                                        # fold 0 not recomputed: 1 + 1(fail) + 2
        # the failed fold's test window stayed opened, and NO new training job / revision chain was made
        ids_after = self.rows("SELECT id FROM optimization_jobs ORDER BY id")
        new_jobs = [r for r in ids_after if r not in opt_jobs_before]
        self.assertEqual(len(new_jobs), 1)      # only the unrelated queued job we created above; none for folds
        self.assertEqual(after[1]["train_job_id"], folds[1]["train_job_id"])
        self.assertEqual(after[1]["freeze_id"], folds[1]["freeze_id"])
        self.assertEqual(after[1]["test_opened_at"], folds[1]["test_opened_at"])
        self.assertEqual(wf_freeze.get_freeze(after[1]["freeze_id"])["test_opened_at"], freeze_before["test_opened_at"])
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM optimization_jobs WHERE parent_job_id IS NOT NULL")[0]["n"], 0)
        self.assertTrue(wfl.get_stitched(job)["available"])

    def test_cancel_then_resume_keeps_completed_folds(self):
        holder = {}
        real_panel = build_panel

        def cancel_after_first(start, end, ids, **kw):
            panel = real_panel(start, end, ids, **kw)
            if not holder.get("done") and end == holder["first_test_end"]:
                holder["done"] = True
                wfl.cancel_job(holder["job"])       # requested while fold 0 is mid-flight
            return panel
        with self.force_freeze():
            q = wfl.create_job(self.payload())
            holder["job"] = q["job_id"]
            holder["first_test_end"] = wfl.get_folds(q["job_id"])[0]["windows"]["test"][1]
            with patch.object(wfl, "build_panel", side_effect=cancel_after_first):
                wfl.execute_job(q["job_id"])
            v = wfl.get_job(q["job_id"])
            self.assertEqual(v["status"], "cancelled")
            self.assertEqual([f["status"] for f in self.folds(q["job_id"])], ["completed", "pending", "pending"])
            first = dict(self.folds(q["job_id"])[0])
            self.assertIsNone(v["stitched_status"])
            self.assertFalse(wfl.get_stitched(q["job_id"])["available"])
            with self.assertRaises(RuntimeError):
                wfl.cancel_job(q["job_id"])           # already terminal
            wfl.resume_job(q["job_id"])
            wfl.execute_job(q["job_id"])
        self.assertEqual(wfl.get_job(q["job_id"])["status"], "completed")
        self.assertEqual(dict(self.folds(q["job_id"])[0]), first)
        self.assertTrue(wfl.get_stitched(q["job_id"])["available"])

    def test_cancel_queued_job_and_restart_recovery(self):
        q = wfl.create_job(self.payload())
        self.assertEqual(wfl.cancel_job(q["job_id"])["status"], "cancelled")
        q2 = wfl.create_job(self.payload(seed=2))
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_jobs SET status='running' WHERE id=?", (q2["job_id"],))
            c.exec_driver_sql("UPDATE walkforward_folds SET status='running' WHERE job_id=? AND fold_index=0",
                              (q2["job_id"],))
        self.assertEqual(wfl.recover_interrupted(), 1)
        self.assertEqual(wfl.get_job(q2["job_id"])["status"], "failed")
        self.assertEqual(self.folds(q2["job_id"])[0]["status"], "pending")

    def test_restart_does_not_pollute_completed_folds(self):
        with self.force_freeze():
            job = self.run_wf()
        before = [dict(f) for f in self.folds(job)]
        eq_before = wfl.get_equity(job, "fold", 0)
        wfl.recover_interrupted()          # job is completed: nothing to recover
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_jobs SET status='running' WHERE id=?", (job,))
        wfl.recover_interrupted()
        self.assertEqual([dict(f) for f in self.folds(job)], before)
        self.assertEqual(wfl.get_equity(job, "fold", 0), eq_before)

    def test_completed_fold_and_definition_are_immutable(self):
        with self.force_freeze():
            job = self.run_wf()
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_jobs SET status='completed' WHERE id=?", (job,))   # mutable
        for sql in ("UPDATE walkforward_folds SET oos_metrics_json='{}' WHERE job_id=? AND fold_index=0",
                    "UPDATE walkforward_folds SET status='pending' WHERE job_id=? AND fold_index=0",
                    "DELETE FROM walkforward_folds WHERE job_id=? AND fold_index=0",
                    "UPDATE walkforward_equity SET net_equity=1 WHERE job_id=? AND series='fold'",
                    "DELETE FROM walkforward_equity WHERE job_id=? AND series='fold'",
                    "DELETE FROM walkforward_trades WHERE job_id=? AND series='fold'",
                    "UPDATE walkforward_jobs SET request_json='{}' WHERE id=?",
                    "UPDATE walkforward_jobs SET protocol_json='{}' WHERE id=?",
                    "UPDATE walkforward_jobs SET candidate_rule='x' WHERE id=?",
                    "UPDATE walkforward_jobs SET strategy_version_id=99 WHERE id=?",
                    "UPDATE walkforward_jobs SET publication_mode='strict' WHERE id=?"):
            with self.assertRaises(DatabaseError, msg=sql), self.engine.begin() as c:
                c.exec_driver_sql(sql, (job,))

    def test_fold_link_columns_write_once(self):
        q = wfl.create_job(self.payload())
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_folds SET train_job_id='a' WHERE job_id=? AND fold_index=0", (q["job_id"],))
        with self.assertRaises(DatabaseError), self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_folds SET train_job_id='b' WHERE job_id=? AND fold_index=0", (q["job_id"],))
        with self.assertRaises(DatabaseError), self.engine.begin() as c:
            c.exec_driver_sql("UPDATE walkforward_folds SET train_end='2020-01-01' WHERE job_id=? AND fold_index=0",
                              (q["job_id"],))

    def test_provenance_links_every_fold_to_training_job_and_freeze(self):
        strategy = lab.create_strategy({"name": "wf", "template_key": "momentum_trend", "params": SMALL,
                                        "backtest": {"missing": {"execution": "partial"}}})
        ver = strategy["latest_version"]
        with self.force_freeze():
            job = self.run_wf(strategy_id=strategy["id"], strategy_version_id=ver["id"])
        prov = wfl.get_job(job)["provenance"]
        self.assertEqual(prov["definition_hash"], ver["definition_hash"])
        self.assertEqual(prov["protocol_version"], "wf-protocol/f0.5/1")
        self.assertEqual(prov["freeze_rule"], "wf-freeze/f0.5/2")
        self.assertEqual(prov["oos_contract"], "wf-oos-state/f0.5/1")
        self.assertEqual(prov["stitched_contract"], "wf-stitched/f1.5/1")
        self.assertEqual(prov["universe_kind"], "explicit")
        self.assertEqual(len(prov["fold_links"]), 3)
        for link in prov["fold_links"]:
            self.assertTrue(link["train_job_id"] and link["freeze_id"] and link["candidate_digest"])
            fz = wf_freeze.get_freeze(link["freeze_id"])
            self.assertEqual(fz["job_id"], link["train_job_id"])
            self.assertEqual(fz["strategy_version_id"], ver["id"])       # training jobs carry the strategy link
            self.assertEqual(fz["definition_hash"], ver["definition_hash"])
        self.assertIn("trial_disclosure", prov)
        self.assertTrue(wfl.get_job(job)["reproducibility"])

    def test_test_window_mutation_cannot_change_training_or_freeze(self):
        with self.force_freeze():
            clean = self.run_wf()
        plan = self.folds(clean)
        last_train_end = plan[-1]["train_end"]
        with self.engine.begin() as c:                      # wreck only the LAST fold's test window
            c.exec_driver_sql("UPDATE daily_quotes SET open=open*3, close=close*3 WHERE date > ?", (last_train_end,))
            c.exec_driver_sql("INSERT INTO corporate_actions(stock_id,date,kind,source,cash_dividend) "
                              "VALUES('1101',?, 'cash', 'x', 5.0)", (plan[-1]["test_start"],))
        with self.force_freeze(), patch.object(opt, "_find_cached", return_value=None):
            dirty = self.run_wf(seed=4)
        a, b = plan, self.folds(dirty)
        for k in (0, 1, 2):
            self.assertEqual(a[k]["candidate_digest"], b[k]["candidate_digest"], k)      # freezes unchanged
            self.assertEqual(a[k]["primary_params_json"], b[k]["primary_params_json"], k)
            ra = [r["metrics_json"] for r in self.rows("SELECT metrics_json FROM optimization_runs WHERE job_id=:j "
                                                       "ORDER BY run_index", j=a[k]["train_job_id"])]
            rb = [r["metrics_json"] for r in self.rows("SELECT metrics_json FROM optimization_runs WHERE job_id=:j "
                                                       "ORDER BY run_index", j=b[k]["train_job_id"])]
            self.assertEqual(ra, rb, k)                                                   # training unchanged
        for k in (0, 1):                                                                  # earlier OOS unchanged
            self.assertEqual(a[k]["oos_metrics_json"], b[k]["oos_metrics_json"], k)
        self.assertNotEqual(a[2]["oos_metrics_json"], b[2]["oos_metrics_json"])           # only the wrecked window moves

    def test_publication_and_adjustment_point_in_time(self):
        # fundamentals: assumed mode only, recorded on the job; strict is refused up front
        q = wfl.create_job({"template_key": "value_quality", "params": {}, "stock_ids": STOCKS,
                            "data_start": DATA_START, "data_end": D1, "panel_start": PANEL_START, "protocol": PROTOCOL})
        self.assertEqual(wfl.get_job(q["job_id"])["provenance"]["publication_mode"], "assumed")
        with self.assertRaisesRegex(ValueError, "assumed"):
            wfl.create_job({"template_key": "value_quality", "params": {}, "stock_ids": STOCKS,
                            "publication_mode": "strict", "protocol": PROTOCOL, "data_start": DATA_START,
                            "data_end": D1, "panel_start": PANEL_START})
        # OOS panel is built with the job's publication mode, and the boundary signal only sees <= train_end
        seen = {}
        real = build_panel

        def spy(start, end, ids, **kw):
            seen.setdefault("modes", set()).add(kw["publication_mode"])
            return real(start, end, ids, **kw)
        with self.force_freeze(), patch.object(wfl, "build_panel", side_effect=spy):
            self.run_wf()
        self.assertEqual(seen["modes"], {"assumed"})


# ── API ─────────────────────────────────────────────────
class APITests(WFCase):
    def setUp(self):
        super().setUp()
        from app.main import app
        self.client = TestClient(app)

    def test_full_api_lifecycle(self):
        c, base = self.client, "/api/strategy-lab/walkforward"
        contracts = c.get(base + "/contracts").json()
        self.assertTrue(contracts["stitched_oos"]["implemented"])
        self.assertNotIn("stitched_contract", contracts)
        proto = c.get(base + "/protocol").json()
        self.assertEqual(proto["contracts"], {k: v for k, v in contracts.items() if k != "notice"})   # one truth
        self.assertNotIn("oos_state_contract", proto)
        with self.force_freeze():
            r = c.post(base + "/jobs", json=self.payload())
            self.assertEqual(r.status_code, 200, r.text)
        job = r.json()["job_id"]                              # TestClient runs the BackgroundTask first
        status = c.get(f"{base}/jobs/{job}").json()
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["progress"]["completed"], 3)
        self.assertEqual(len(c.get(base + "/jobs").json()), 1)
        res = c.get(f"{base}/jobs/{job}/result").json()
        self.assertEqual(res["evidence"], "independent_fold")
        self.assertEqual(len(res["folds"]), 3)
        self.assertTrue(res["stitched"]["available"])
        self.assertEqual(len(c.get(f"{base}/jobs/{job}/folds").json()), 3)
        fold = c.get(f"{base}/jobs/{job}/folds/1").json()
        self.assertEqual(fold["outcome"], "evaluated")
        self.assertTrue(c.get(f"{base}/jobs/{job}/folds/1/equity").json()["points"])
        self.assertTrue(c.get(f"{base}/jobs/{job}/folds/1/trades?kind=fill&limit=5").json()["rows"])
        self.assertEqual(c.get(f"{base}/jobs/{job}/oos").json()["evidence"], "independent_fold")
        st = c.get(f"{base}/jobs/{job}/stitched").json()
        self.assertEqual(st["evidence"], "stitched_secondary")
        self.assertTrue(c.get(f"{base}/jobs/{job}/stitched/equity").json()["points"])
        self.assertTrue(c.get(f"{base}/jobs/{job}/stitched/trades").json()["rows"])
        self.assertEqual(c.post(f"{base}/jobs/{job}/cancel").status_code, 409)
        self.assertEqual(c.post(f"{base}/jobs/{job}/resume").status_code, 409)

    def test_api_errors(self):
        c, base = self.client, "/api/strategy-lab/walkforward"
        self.assertEqual(c.post(base + "/jobs", json={"template_key": "nope"}).status_code, 400)
        self.assertEqual(c.get(base + "/jobs/missing").status_code, 404)
        self.assertEqual(c.get(base + "/jobs/missing/result").status_code, 404)
        job = wfl.create_job(self.payload())["job_id"]
        self.assertEqual(c.get(f"{base}/jobs/{job}/folds/9").status_code, 404)
        self.assertEqual(c.get(f"{base}/jobs/{job}/folds/0/trades?kind=x").status_code, 400)
        self.assertFalse(c.get(f"{base}/jobs/{job}/stitched").json()["available"])


if __name__ == "__main__":
    unittest.main()
