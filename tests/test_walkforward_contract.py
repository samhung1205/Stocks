"""Stage F0 tests: walk-forward protocol, candidate freeze / leakage guards, partial-rebalance
execution policy, optimization provenance + revisions, warning severity contract.

Offline (temp SQLite, synthetic data).
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import DatabaseError

sys.path.insert(0, str(Path(__file__).parent))
from test_optimization_backend import (END, JobCase, SMALL, SPACE, START, STOCKS, WINDOW)  # noqa: E402

from app.db import query_all, query_one, upsert_many  # noqa: E402
from app.db import tables as t  # noqa: E402
from app.db.migrations import migrate  # noqa: E402
from app.services import optimization_lab as opt  # noqa: E402
from app.services import strategy_lab as lab  # noqa: E402
from app.services import warning_contract as wc  # noqa: E402
from app.services.strategy import run_backtest  # noqa: E402
from app.services.strategy.adjustment import adjust_bars  # noqa: E402
from app.services.strategy.panel import build_panel  # noqa: E402
from app.services.strategy.rules import static_signals  # noqa: E402
from app.services.strategy.types import (BacktestConfig, Bar, CostConfig, ExecutionRule,  # noqa: E402
                                         MissingDataRule, Panel, PanelDay, Security)
from app.services.walkforward import freeze as wf_freeze  # noqa: E402
from app.services.walkforward import oos as wf_oos  # noqa: E402
from app.services.walkforward import protocol as wf  # noqa: E402
from app.services.strategy.signals import TemplateSignalRule  # noqa: E402

TRAIN_END = "2025-02-14"
FOLD = {"train_start": WINDOW, "train_end": TRAIN_END, "test_start": "2025-02-17"}


def _sessions(start, end):
    d, out = dt.date.fromisoformat(start), []
    while d.isoformat() <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# ── protocol ────────────────────────────────────────────
class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.days = _sessions("2022-11-01", "2026-09-18")

    def test_default_protocol_on_current_window(self):
        r = wf.generate_folds("2022-11-01", "2026-09-18", self.days)
        folds = r["folds"]
        self.assertEqual(len(folds), 4)
        self.assertEqual([f["truncated"] for f in folds], [False, False, False, True])
        self.assertEqual(folds[0]["train_end"], "2024-10-31")
        self.assertEqual(folds[-1]["test_end"], "2026-09-18")
        self.assertIn("PARTIAL_FOLD_TEST_WINDOW", r["notes"])
        for a, b in zip(folds, folds[1:]):
            self.assertLess(a["test_end"], b["test_start"])           # tests never overlap
        for f in folds:
            self.assertLess(f["train_end"], f["test_start"])
            self.assertEqual(f["train_start"], "2022-11-01")           # expanding
            self.assertLessEqual(f["test_end"], "2026-09-18")

    def test_gap_moves_train_end_and_rolling_needs_length(self):
        r = wf.generate_folds("2022-11-01", "2026-09-18", self.days, {"gap_sessions": 5})
        f = r["folds"][0]
        between = [d for d in self.days if f["train_end"] < d < f["test_start"]]
        self.assertEqual(len(between), 5)
        with self.assertRaises(ValueError):
            wf.generate_folds("2022-11-01", "2026-09-18", self.days, {"mode": "rolling"})
        roll = wf.generate_folds("2022-11-01", "2026-09-18", self.days,
                                 {"mode": "rolling", "rolling_train_months": 18})
        self.assertGreater(roll["folds"][1]["train_start"], "2022-11-01")

    def test_validation_and_short_final_window_dropped(self):
        for bad in ({"step_months": 3}, {"unknown": 1}, {"test_months": 0}, {"mode": "x"},
                    {"execution_policy": "maybe"}):
            with self.assertRaises(ValueError):
                wf.validate(bad)
        r = wf.generate_folds("2022-11-01", "2026-06-30", _sessions("2022-11-01", "2026-06-30"))
        self.assertEqual(len(r["folds"]), 3)   # 2026-05..06 (< 3 months) is dropped, not evaluated
        self.assertEqual(len(r["dropped"]), 1)


# ── warning severity ────────────────────────────────────
class WarningContractTests(unittest.TestCase):
    def test_known_engine_warnings_map_to_stable_codes(self):
        cases = {
            "UNRESOLVED_CORPORATE_ACTION 2025-01-06 1101: x": ("UNRESOLVED_CORPORATE_ACTION", "critical"),
            "2025-01-06: execution price missing for 1101; cancelled": ("EXECUTION_CANCELLED", "critical"),
            "2025-01-06: execution price missing for 1101; deferred": ("EXECUTION_DEFERRED", "caution"),
            "2025-01-06: PARTIAL_REBALANCE execution price missing for 1101; frozen, rest executed":
                ("PARTIAL_REBALANCE", "caution"),
            "2025-01-06: newer signal replaced unfilled target order": ("TARGET_ORDER_REPLACED", "caution"),
            "2025-01-06 1101: valuation is 7 sessions stale": ("STALE_VALUATION", "caution"),
            "2025-01-06 1101: no valuation price": ("NO_VALUATION_PRICE", "critical"),
            "2025-01-06: signal falls beyond backtest execution window": ("SIGNAL_BEYOND_WINDOW", "info"),
            "adjusted-price residual jump >25% on 2 ex-date(s)": ("ADJUSTED_PRICE_RESIDUAL_JUMP", "caution"),
            "assumed publication mode: assumed_deadline fundamentals": ("ASSUMED_PUBLICATION_MODE", "caution"),
        }
        for text, (code, sev) in cases.items():
            got = wc.classify(text)
            self.assertEqual((got["code"], got["severity"]), (code, sev), text)

    def test_unknown_defaults_to_caution_never_info(self):
        got = wc.classify("something the engine never said before")
        self.assertEqual((got["code"], got["severity"]), ("UNCLASSIFIED", "caution"))

    def test_summary_and_catalog(self):
        s = wc.classify_all(["a new thing", "2025-01-06: signal falls beyond backtest execution window",
                             "UNRESOLVED_CORPORATE_ACTION x"])
        self.assertEqual(s["max_severity"], "critical")
        self.assertEqual(s["counts"], {"info": 1, "caution": 1, "critical": 1})
        self.assertEqual(s["codes"][0]["severity"], "critical")   # worst-first
        cat = wc.catalog()
        self.assertEqual(cat["severities"], ["info", "caution", "critical"])
        self.assertTrue(all(x["severity"] in wc.SEVERITIES for x in cat["structural"]))
        with self.assertRaises(KeyError):
            wc.structural("NOPE")

    def test_engine_emitted_warnings_are_all_classified(self):
        panel = _panel({"A": [10] * 5, "B": [10, 10, None, None, 10]})
        r = run_backtest(panel, static_signals({D[0]: {"A": .5, "B": .5}, D[2]: {"A": .5, "B": .5}}),
                         _cfg("next_available"))
        self.assertTrue(r["warnings"])
        for w in r["warnings"]:
            self.assertNotEqual(wc.classify(w)["code"], "UNCLASSIFIED", w)


# ── execution policy ────────────────────────────────────
D = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10", "2025-01-13"]


def _panel(prices, dates=None):
    dates = dates or D[:len(next(iter(prices.values())))]
    days = []
    for i, d in enumerate(dates):
        bars = {sid: Bar(sid, d, open=p[i], close=p[i]) for sid, p in prices.items() if p[i] is not None}
        days.append(PanelDay(d, bars))
    return Panel(tuple(days), {sid: Security(sid) for sid in prices}, "strict")


def _cfg(execution, cash=10_000):
    return BacktestConfig(initial_cash=cash, execution=ExecutionRule("open", 1),
                          missing=MissingDataRule(execution=execution),
                          costs=CostConfig(0, 0, 0, 0, 0))


class ExecutionPolicyTests(unittest.TestCase):
    SIGNALS = {D[0]: {"A": .4, "B": .3, "C": .3}, D[2]: {"A": .25, "B": .25, "C": .25}}

    def panel(self):   # B is suspended on d3 (the day the second target executes)
        return _panel({"A": [10] * 6, "B": [10, 10, 10, None, 10, 10], "C": [10] * 6})

    def test_all_or_nothing_defers_whole_order(self):
        r = run_backtest(self.panel(), static_signals(self.SIGNALS), _cfg("next_available"))
        self.assertEqual({f["date"] for f in r["fills"]} & {D[3]}, set())     # nothing traded on d3
        self.assertIn(D[4], {f["date"] for f in r["fills"]})                  # executed a session later
        self.assertEqual(r["metadata"]["partial_rebalance_count"], 0)

    def test_partial_freezes_suspended_holding_and_trades_the_rest(self):
        r = run_backtest(self.panel(), static_signals(self.SIGNALS), _cfg("partial"))
        d3 = [f for f in r["fills"] if f["date"] == D[3]]
        self.assertEqual({f["stock_id"] for f in d3}, {"A", "C"})
        self.assertNotIn("B", {f["stock_id"] for f in d3})
        ev = r["metadata"]["partial_rebalances"]
        self.assertEqual((len(ev), ev[0]["frozen"], ev[0]["date"]), (1, ["B"], D[3]))
        self.assertTrue(any("PARTIAL_REBALANCE" in w for w in r["warnings"]))
        # target-weight drift: B keeps its old 30% (> 25% target); A, C trimmed; the rest is cash.
        pos = r["open_positions"]
        self.assertEqual((pos["A"], pos["B"], pos["C"]), (250, 300, 250))
        self.assertEqual(r["ending_cash"], 2_000)     # 20% cash: freed by trimming A and C, not re-spent on B

    def test_no_cash_or_shares_created_from_nothing(self):
        # Flat prices + zero cost: equity must equal initial cash on every day, on both ledgers,
        # and buys - sells must equal the cash actually spent.
        r = run_backtest(self.panel(), static_signals(self.SIGNALS), _cfg("partial"))
        for p in r["equity_curve"]:
            self.assertEqual((p["net_equity"], p["gross_equity"]), (10_000, 10_000), p["date"])
        spent = sum(f["shares"] * f["reference_price"] * (1 if f["side"] == "buy" else -1) for f in r["fills"])
        self.assertAlmostEqual(10_000 - r["ending_cash"], spent)
        self.assertGreaterEqual(r["ending_cash"], 0)
        shares_bought = sum(f["shares"] for f in r["fills"] if f["side"] == "buy")
        shares_sold = sum(f["shares"] for f in r["fills"] if f["side"] == "sell")
        self.assertEqual(shares_bought - shares_sold, sum(r["open_positions"].values()))

    def test_frozen_holding_cannot_be_sold_even_when_target_is_zero(self):
        sig = {D[0]: {"A": .5, "B": .5}, D[2]: {"A": 1.0}}    # wants out of B while B is suspended
        p = _panel({"A": [10] * 6, "B": [10, 10, 10, None, 10, 10]})
        r = run_backtest(p, static_signals(sig), _cfg("partial"))
        self.assertEqual([f for f in r["fills"] if f["date"] == D[3]], [])   # B can't be sold, so A has no cash
        self.assertEqual((r["open_positions"], r["ending_cash"]), ({"A": 500, "B": 500}, 0))
        self.assertEqual(r["metadata"]["partial_rebalance_count"], 1)

    def test_frozen_unheld_target_stays_cash(self):
        sig = {D[0]: {"A": .5}, D[2]: {"A": .5, "B": .5}}   # B is new AND suspended on execution day
        p = _panel({"A": [10] * 6, "B": [10, 10, 10, None, 10, 10]})
        r = run_backtest(p, static_signals(sig), _cfg("partial"))
        self.assertNotIn("B", {f["stock_id"] for f in r["fills"] if f["date"] == D[3]})
        self.assertEqual(r["ending_cash"], 5_000)          # its weight stays cash, not redistributed
        self.assertEqual(r["open_positions"], {"A": 500})

    def test_no_tradable_name_defers_like_next_available(self):
        p = _panel({"A": [10, 10, 10, None, 10, 10]})
        r = run_backtest(p, static_signals({D[2]: {"A": 1.0}}), _cfg("partial"))
        self.assertEqual([f["date"] for f in r["fills"]], [D[4]])
        self.assertEqual(r["metadata"]["partial_rebalance_count"], 0)

    def test_without_missing_prices_partial_equals_next_available(self):
        p = _panel({"A": [10, 11, 12, 11, 13, 12], "B": [10, 10, 9, 9, 10, 11]})
        sig = {D[0]: {"A": .5, "B": .5}, D[2]: {"A": .3, "B": .7}}
        a = run_backtest(p, static_signals(sig), _cfg("next_available"))
        b = run_backtest(p, static_signals(sig), _cfg("partial"))
        self.assertEqual((a["fills"], a["equity_curve"]), (b["fills"], b["equity_curve"]))

    def test_stage_b_default_unchanged_and_invalid_value_rejected(self):
        self.assertEqual(MissingDataRule().execution, "next_available")
        self.assertEqual(BacktestConfig().missing.execution, "next_available")
        with self.assertRaises(ValueError):
            MissingDataRule(execution="whatever")

    def test_turnover_and_cost_under_partial(self):
        cost = BacktestConfig(initial_cash=10_000, missing=MissingDataRule(execution="partial"),
                              costs=CostConfig(0.001425, 1.0, 0, 0.003, 0))
        r = run_backtest(self.panel(), static_signals(self.SIGNALS), cost)
        self.assertGreater(sum(f["commission"] + f["tax"] for f in r["fills"]), 0)
        self.assertLess(r["ending_cash"] + sum(s * 10 for s in r["open_positions"].values()), 10_000)


# ── freeze / leakage ────────────────────────────────────
def _analysis(label, peak="p1", region=("p1", "p2"), edge=()):
    return {"plateau": {"label": label, "peak_key": peak, "region_keys": list(region),
                        "edge_of_search_range": list(edge)}}


PTS = [{"key": "p1", "params": {"a": 1}, "value": 1.0}, {"key": "p2", "params": {"a": 2}, "value": .9},
       {"key": "p3", "params": {"a": 3}, "value": .1}]


def _line(n, peak_at, turnover=None):
    """Chain a1-a2-...-an (each adjacent pair are neighbours) with the IS peak at index ``peak_at``."""
    keys = [f"a{i}" for i in range(1, n + 1)]
    pts = [{"key": k, "params": {"a": i}, "value": 1.0 if i == peak_at else .9 - .01 * i,
            "metrics": {"turnover": (turnover or {}).get(k, 1.0)}} for i, k in enumerate(keys, 1)]
    graph = {k: [x for x in (keys[i - 1] if i else None, keys[i + 1] if i < n - 1 else None) if x]
             for i, k in enumerate(keys)}
    return keys, pts, graph


class SelectionRuleTests(unittest.TestCase):
    G = {"p1": ["p2"], "p2": ["p1"]}

    def test_plateau_freezes_region_and_declares_primary_up_front(self):
        sel = wf_freeze.select_candidates(_analysis("plateau", edge=["a"]), PTS, self.G)
        self.assertTrue(sel["freezable"])
        self.assertEqual((sel["primary_key"], [r["key"] for r in sel["region"]]), ("p1", ["p1", "p2"]))
        self.assertEqual(sel["edge_axes"], ["a"])                       # edge contract unchanged
        self.assertEqual([w["code"] for w in sel["warnings"]], ["EDGE_OF_SEARCH_RANGE"])
        self.assertEqual(sel["rule_version"], "wf-freeze/f0.5/2")

    def test_primary_is_the_medoid_not_the_is_peak(self):
        keys, pts, graph = _line(5, peak_at=1)
        moved = []
        for peak in (1, 5, 3, 2):
            keys, pts, graph = _line(5, peak_at=peak)
            sel = wf_freeze.select_candidates(_analysis("plateau", peak=f"a{peak}", region=keys), pts, graph)
            moved.append(sel["primary_key"])
            self.assertEqual(sel["is_peak_key"], f"a{peak}")
            self.assertEqual(len(sel["region"]), 5)                     # region still saved in full
        self.assertEqual(moved, ["a3"] * 4)     # spike moves around; representative does not

    def test_medoid_is_deterministic_under_input_order(self):
        keys, pts, graph = _line(5, peak_at=2)
        base = wf_freeze.select_candidates(_analysis("plateau", peak="a2", region=keys), pts, graph)
        rev = wf_freeze.select_candidates(_analysis("plateau", peak="a2", region=list(reversed(keys))),
                                          list(reversed(pts)), {k: list(reversed(v)) for k, v in graph.items()})
        self.assertEqual((base["primary_key"], [r["key"] for r in base["region"]]),
                         (rev["primary_key"], [r["key"] for r in rev["region"]]))
        self.assertEqual(wf_freeze.candidate_digest("t", "sharpe", base),
                         wf_freeze.candidate_digest("t", "sharpe", rev))

    def test_tie_break_turnover_then_objective_then_key(self):
        def pick(t1, t2, v1=1.0, v2=.9, higher=True, k=("x", "y")):
            pts = [{"key": k[0], "params": {}, "value": v1, "metrics": {"turnover": t1}},
                   {"key": k[1], "params": {}, "value": v2, "metrics": {"turnover": t2}}]
            g = {k[0]: [k[1]], k[1]: [k[0]]}          # equal distance sums by symmetry
            return wf_freeze.region_medoid(list(k), g, {p["key"]: p for p in pts}, higher)[0]
        self.assertEqual(pick(5, 2), "y")                      # lower turnover beats better objective
        self.assertEqual(pick(2, 2, 1.0, .9), "x")             # equal turnover -> better objective
        self.assertEqual(pick(2, 2, .9, 1.0), "y")
        self.assertEqual(pick(2, 2, .9, 1.0, higher=False), "x")   # direction-aware
        self.assertEqual(pick(2, 2, 1.0, 1.0, k=("b", "a")), "a")  # everything tied -> stable point_key
        self.assertEqual(pick(None, 9), "y")                   # missing turnover is worst

    def test_medoid_prefers_centrality_over_turnover(self):
        keys, pts, graph = _line(3, peak_at=1, turnover={"a1": 0.1, "a2": 9.0, "a3": 0.1})
        self.assertEqual(wf_freeze.select_candidates(_analysis("plateau", region=keys, peak="a1"),
                                                     pts, graph)["primary_key"], "a2")

    def test_non_plateau_is_not_freezable_by_default(self):
        for label in ("isolated_peak", "scattered", "flat", "insufficient_data"):
            sel = wf_freeze.select_candidates(_analysis(label), PTS, self.G)
            self.assertFalse(sel["freezable"])
            self.assertIn("NO_STABLE_CANDIDATE", sel["reasons"])
        forced = wf_freeze.select_candidates(_analysis("isolated_peak", edge=["a"]), PTS, self.G,
                                             require_plateau=False)
        self.assertTrue(forced["freezable"])
        self.assertEqual((len(forced["region"]), forced["primary_key"]), (1, "p1"))   # single IS point
        self.assertTrue(forced["reasons"][0].startswith("FROZEN_WITHOUT_PLATEAU"))
        self.assertEqual({w["code"] for w in forced["warnings"]},
                         {"FROZEN_WITHOUT_PLATEAU", "ISOLATED_PEAK", "EDGE_OF_SEARCH_RANGE"})
        for label, code in (("scattered", "SCATTERED_SURFACE"), ("flat", "FLAT_SURFACE"),
                            ("insufficient_data", "INSUFFICIENT_ROBUSTNESS_DATA")):
            f = wf_freeze.select_candidates(_analysis(label), PTS, self.G, require_plateau=False)
            self.assertIn(code, {w["code"] for w in f["warnings"]})

    def test_digest_ignores_ids_and_depends_on_content(self):
        a = wf_freeze.select_candidates(_analysis("plateau"), PTS, self.G)
        b = wf_freeze.select_candidates(_analysis("plateau"), PTS, self.G)
        self.assertEqual(wf_freeze.candidate_digest("t", "sharpe", a), wf_freeze.candidate_digest("t", "sharpe", b))
        b["region"][0]["value"] = 1.1
        self.assertNotEqual(wf_freeze.candidate_digest("t", "sharpe", a), wf_freeze.candidate_digest("t", "sharpe", b))


class FreezeTests(JobCase):
    def train_job(self, **extra):
        return self.run_job(research_end=TRAIN_END, **extra)

    def freeze(self, job, fold=FOLD):
        return wf_freeze.freeze_from_job(job, fold, require_plateau=False)

    def test_test_window_data_cannot_change_training_or_frozen_candidates(self):
        job_a = self.train_job()
        fz_a = self.freeze(job_a)
        rows_a = [r["metrics_json"] for r in self.runs(job_a)]
        with self.engine.begin() as c:   # wreck everything after the training end
            c.exec_driver_sql("UPDATE daily_quotes SET open=open*3, close=close*3 WHERE date > ?", (TRAIN_END,))
            c.exec_driver_sql("DELETE FROM daily_quotes WHERE stock_id='1101' AND date > '2025-02-20'")
        # (a) the data fingerprint is bounded by research_end, so the change is invisible to the cache key
        with self.engine.connect() as c:
            fp = lab.data_fingerprint(c, STOCKS, START, TRAIN_END)
        self.assertEqual(fp, json.loads(opt.get_job(job_a)["reproducibility"]["data_fingerprint"]
                                        if isinstance(opt.get_job(job_a)["reproducibility"]["data_fingerprint"], str)
                                        else json.dumps(opt.get_job(job_a)["reproducibility"]["data_fingerprint"])))
        # (b) and even when the cache is defeated and everything is recomputed on the mutated DB
        with patch.object(opt, "_find_cached", return_value=None):
            job_b = self.train_job()
        self.assertNotEqual(job_a, job_b)
        self.assertEqual(rows_a, [r["metrics_json"] for r in self.runs(job_b)])
        self.assertTrue(all(r["cached_from_job"] is None for r in self.runs(job_b)))
        fz_b = self.freeze(job_b)
        self.assertEqual(fz_a["candidate_digest"], fz_b["candidate_digest"])
        self.assertEqual(fz_a["primary_params"], fz_b["primary_params"])
        self.assertEqual(fz_a["primary_point_key"], fz_b["primary_point_key"])
        self.assertEqual(fz_a["rule_version"], "wf-freeze/f0.5/2")
        if fz_a["plateau_label"] != "plateau":     # surface label warning travels with the freeze
            self.assertIn("FROZEN_WITHOUT_PLATEAU", {w["code"] for w in fz_a["warnings"]})

    def test_panel_for_training_job_contains_no_test_rows(self):
        with patch.object(opt, "build_panel", wraps=build_panel) as spy:
            self.train_job()
        panel_end = spy.call_args.args[1]
        self.assertEqual(panel_end, TRAIN_END)

    def test_fold_window_mismatch_and_incomplete_job_refused(self):
        job = self.train_job()
        with self.assertRaisesRegex(ValueError, "TRAIN_WINDOW_MISMATCH"):
            self.freeze(job, {**FOLD, "train_end": "2025-02-21", "test_start": "2025-02-24"})
        full = self.run_job()   # window extends to END, i.e. through the test window
        with self.assertRaisesRegex(ValueError, "TRAIN_WINDOW_MISMATCH"):
            self.freeze(full)
        q = opt.create_job(self.payload(research_end="2025-02-13"))   # queued, never executed
        with self.assertRaisesRegex(ValueError, "completed"):
            wf_freeze.freeze_from_job(q["job_id"])

    def test_freeze_is_idempotent_and_write_once(self):
        job = self.train_job()
        first = self.freeze(job)
        again = wf_freeze.freeze_from_job(job, FOLD)
        self.assertEqual((first["id"], again["created"]), (again["id"], False))
        for sql in ("UPDATE wf_candidate_freezes SET primary_point_key='x'",
                    "UPDATE wf_candidate_freezes SET candidate_digest='x'",
                    "DELETE FROM wf_candidate_freezes"):
            with self.assertRaises(DatabaseError), self.engine.begin() as c:
                c.exec_driver_sql(sql)
        opened = wf_freeze.open_test_window(first["id"])
        stamp = opened["test_opened_at"]
        self.assertIsNotNone(stamp)
        self.assertEqual(wf_freeze.open_test_window(first["id"])["test_opened_at"], stamp)  # once
        with self.assertRaises(DatabaseError), self.engine.begin() as c:
            c.exec_driver_sql("UPDATE wf_candidate_freezes SET test_opened_at=NULL")


# ── provenance / revision ───────────────────────────────
class ProvenanceTests(JobCase):
    def strategy(self):
        s = lab.create_strategy({"name": "s", "template_key": "momentum_trend", "params": SMALL})
        return s["id"], s["latest_version"]

    def job_row(self, job):
        with self.engine.connect() as c:
            return query_one(c, "SELECT * FROM optimization_jobs WHERE id=:i", i=job)

    def test_strategy_version_provenance_persisted(self):
        sid, ver = self.strategy()
        job = self.run_job(strategy_id=sid, strategy_version_id=ver["id"])
        row = self.job_row(job)
        self.assertEqual((row["strategy_id"], row["strategy_version_id"], row["strategy_version_no"]),
                         (sid, ver["id"], ver["version_no"]))
        self.assertEqual(row["definition_hash"], ver["definition_hash"])
        self.assertEqual((row["parent_job_id"], row["root_job_id"], row["revision_no"]), (None, job, 0))
        prov = opt.get_job(job)["provenance"]
        self.assertTrue(prov["known"])
        self.assertEqual(prov["strategy_version_no"], ver["version_no"])
        by_no = self.run_job(strategy_id=sid, strategy_version_no=ver["version_no"])
        self.assertEqual(self.job_row(by_no)["strategy_version_id"], ver["id"])

    def test_definition_that_is_not_the_version_is_refused(self):
        sid, ver = self.strategy()
        with self.assertRaisesRegex(ValueError, "differs"):
            opt.create_job(self.payload(strategy_id=sid, strategy_version_id=ver["id"],
                                        params={**SMALL, "selection": {"top_n": 3}}))
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(strategy_id=sid))          # id without version
        with self.assertRaises(ValueError):
            opt.create_job(self.payload(strategy_id=sid, strategy_version_id=999))

    def test_job_without_strategy_still_gets_definition_hash(self):
        row = self.job_row(self.run_job())
        self.assertIsNone(row["strategy_id"])
        self.assertIsNotNone(row["definition_hash"])

    def test_provenance_is_immutable_but_status_updates_work(self):
        sid, ver = self.strategy()
        job = self.run_job(strategy_id=sid, strategy_version_id=ver["id"])
        for col, val in (("strategy_id", 99), ("definition_hash", "x"), ("root_job_id", "x"),
                         ("revision_no", 5), ("revision_reason", "x")):
            with self.assertRaises(DatabaseError, msg=col), self.engine.begin() as c:
                c.exec_driver_sql(f"UPDATE optimization_jobs SET {col}=? WHERE id=?", (val, job))
        with self.engine.begin() as c:   # non-provenance columns stay writable
            c.exec_driver_sql("UPDATE optimization_jobs SET error='e' WHERE id=?", (job,))
        # a no-op assignment of the same value is not a change
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE optimization_jobs SET strategy_id=strategy_id WHERE id=?", (job,))

    def test_legacy_jobs_stay_null_and_migration_is_rerunnable(self):
        job = self.run_job()
        with self.engine.begin() as c:   # simulate a pre-F0 database
            for name in list(__import__("app.db.migrations", fromlist=["TRIGGERS"]).TRIGGERS):
                c.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
            for col in ("strategy_id", "strategy_version_id", "strategy_version_no", "definition_hash",
                        "parent_job_id", "root_job_id", "revision_no", "revision_kind",
                        "revision_reason", "revision_json"):
                c.exec_driver_sql(f"ALTER TABLE optimization_jobs DROP COLUMN {col}")
            c.exec_driver_sql("DELETE FROM schema_migrations WHERE version=3")
        migrate(self.engine)
        migrate(self.engine)   # idempotent
        row = self.job_row(job)
        self.assertTrue(all(row[c] is None for c in ("strategy_id", "definition_hash", "root_job_id",
                                                     "parent_job_id", "revision_no")))
        view = opt.get_job(job)["provenance"]
        self.assertFalse(view["known"])
        self.assertEqual(view["warnings"][0]["code"], "LEGACY_JOB_NO_PROVENANCE")
        with self.assertRaises(DatabaseError), self.engine.begin() as c:   # guards are back
            c.exec_driver_sql("UPDATE optimization_jobs SET strategy_id=1 WHERE id=?", (job,))
        self.assertEqual(query_one(self.engine.connect(), "SELECT COUNT(*) n FROM schema_migrations")["n"], 7)


class RevisionTests(JobCase):
    WIDER = {"axes": {**SPACE["axes"], "selection.top_n": [1, 2, 3]}}

    def snapshot(self, job):
        with self.engine.connect() as c:
            j = query_one(c, "SELECT * FROM optimization_jobs WHERE id=:i", i=job)
            runs = query_all(c, "SELECT * FROM optimization_runs WHERE job_id=:i ORDER BY run_index", i=job)
        return j, runs

    def test_revision_is_a_new_job_and_never_overwrites_the_parent(self):
        parent = self.run_job()
        before = self.snapshot(parent)
        child = self.run_job(parent_job_id=parent, revision_reason="top point on edge of top_n",
                             search_space=self.WIDER)
        self.assertNotEqual(parent, child)
        self.assertEqual(before, self.snapshot(parent))        # parent row + runs byte-identical
        prov = opt.get_job(child)["provenance"]
        self.assertEqual((prov["parent_job_id"], prov["root_job_id"], prov["revision_no"],
                          prov["revision_kind"]), (parent, parent, 1, "search_space_expansion"))
        self.assertEqual(prov["revision"]["space_diff"]["axes"], {"selection.top_n": {"added": [3], "removed": []}})
        self.assertTrue(prov["revision"]["space_diff"]["is_pure_expansion"])
        self.assertEqual(opt.get_job(parent)["provenance"]["revision_no"], 0)
        grand = self.run_job(parent_job_id=child, revision_reason="again",
                             search_space={"axes": {**self.WIDER["axes"], "selection.top_n": [1, 2, 3, 4]}})
        gp = opt.get_job(grand)["provenance"]
        self.assertEqual((gp["root_job_id"], gp["revision_no"]), (parent, 2))
        self.assertEqual(opt.get_job(child)["search"]["grid_size"], 12)   # child untouched by grandchild

    def test_revision_may_change_only_the_search_space(self):
        parent = self.run_job()
        for extra, msg in (({"research_end": "2025-02-20"}, "research_end"),
                           ({"objective": "calmar"}, "objective"),
                           ({"params": {**SMALL, "selection": {"top_n": 1}}}, "params"),
                           ({"publication_mode": "strict"}, "publication_mode")):
            with self.assertRaisesRegex(ValueError, msg):
                opt.create_job(self.payload(parent_job_id=parent, revision_reason="r",
                                            search_space=self.WIDER, **extra))
        with self.assertRaisesRegex(ValueError, "revision_reason"):
            opt.create_job(self.payload(parent_job_id=parent, search_space=self.WIDER))
        with self.assertRaisesRegex(ValueError, "does not change"):
            opt.create_job(self.payload(parent_job_id=parent, revision_reason="r"))
        with self.assertRaises(LookupError):
            opt.create_job(self.payload(parent_job_id="nope", revision_reason="r", search_space=self.WIDER))
        q = opt.create_job(self.payload(search_space={"axes": {"selection.top_n": [1, 2]}}))
        with self.assertRaisesRegex(ValueError, "completed"):     # unfinished parent
            opt.create_job(self.payload(parent_job_id=q["job_id"], revision_reason="r",
                                        search_space=self.WIDER))

    def test_no_revision_after_the_parents_test_window_was_opened(self):
        parent = self.run_job(research_end=TRAIN_END)
        fz = wf_freeze.freeze_from_job(parent, FOLD, require_plateau=False)
        # before the test window opens, expansion is a legitimate in-sample revision
        ok = opt.create_job(self.payload(research_end=TRAIN_END, parent_job_id=parent,
                                         revision_reason="edge", search_space=self.WIDER))
        self.assertTrue(ok["queued"])
        wf_freeze.open_test_window(fz["id"])
        with self.assertRaisesRegex(ValueError, "TEST_WINDOW_ALREADY_OPENED"):
            opt.create_job(self.payload(research_end=TRAIN_END, parent_job_id=parent, revision_reason="edge2",
                                        search_space={"axes": {**SPACE["axes"], "selection.top_n": [1, 2, 4]}}))
        # a revision of the revision inherits the closed chain too
        self.assertTrue(self.job_chain_closed(ok["job_id"]))

    def job_chain_closed(self, job):
        with self.engine.connect() as c:
            root = query_one(c, "SELECT root_job_id r FROM optimization_jobs WHERE id=:i", i=job)["r"]
            return opt.chain_test_opened(c, root)

    def test_severity_attached_to_robustness(self):
        job = self.run_job()
        sev = opt.get_robustness(job)["severity"]
        codes = {i["code"] for i in sev["items"]}
        self.assertIn("IN_SAMPLE_ONLY", codes)
        self.assertIn(sev["max_severity"], wc.SEVERITIES)
        self.assertEqual(set(sev["counts"]), set(wc.SEVERITIES))


# ── OOS initial-state contract ──────────────────────────
class OOSStateTests(JobCase):
    TEST_END = "2025-02-28"

    def setUp(self):
        super().setUp()
        params, backtest, self.resolved = lab.validate_definition(
            "momentum_trend", SMALL, {"missing": {"execution": "partial"}})
        self.config = lab.build_backtest_config(backtest, "momentum_trend", params)

    def fold(self, train_end, test_start):
        return {"index": 0, "train_start": WINDOW, "train_end": train_end, "test_start": test_start,
                "test_end": self.TEST_END, "gap_sessions": 0}

    def panel(self):
        return build_panel(START, self.TEST_END, STOCKS, publication_mode="strict",
                           db_engine=self.engine, benchmark_id=None)

    def rule(self):
        return TemplateSignalRule("momentum_trend", self.resolved, None)

    def run_fold(self, train_end="2025-02-14", test_start="2025-02-17"):
        return wf_oos.run_oos_fold(self.panel(), self.rule(), self.config, self.fold(train_end, test_start))

    def test_starts_from_cash_and_has_no_fills_before_test_start(self):
        out = self.run_fold()
        res = out["result"]
        self.assertEqual(out["initial_state"], "cash")
        self.assertTrue(res["fills"])
        self.assertGreaterEqual(min(f["date"] for f in res["fills"]), "2025-02-17")
        pre = [p for p in res["equity_curve"] if p["date"] < "2025-02-17"]
        self.assertTrue(pre)
        self.assertTrue(all(p["net_equity"] == self.config.initial_cash for p in pre))   # flat cash
        # contrast: an ordinary replay of the same candidate DOES trade during the training period
        replay = run_backtest(self.panel(), self.rule(), self.config)
        self.assertTrue([f for f in replay["fills"] if f["date"] < "2025-02-17"])
        self.assertEqual(out["fills_before_test_start"], 0)

    def test_boundary_target_from_train_end_executes_on_first_test_session(self):
        for train_end, test_start in (("2025-02-14", "2025-02-17"),   # boundary on a rebalance day
                                      ("2025-02-13", "2025-02-14")):  # Thursday: NOT a weekly rebalance day
            out = self.run_fold(train_end, test_start)
            self.assertEqual(out["boundary_date"], train_end)
            self.assertIsNotNone(out["boundary_signal"], train_end)
            self.assertEqual(out["first_fill_date"], test_start, train_end)   # earliest T+1, never earlier

    def test_entry_costs_are_charged_in_oos(self):
        out = self.run_fold()
        res = out["result"]
        first = [f for f in res["fills"] if f["date"] == out["first_fill_date"]]
        self.assertTrue(all(f["commission"] > 0 for f in first))
        self.assertGreater(out["metrics"]["gross_return"] - out["metrics"]["net_return"], 0)
        self.assertGreater(out["metrics"]["turnover"], 0)
        self.assertLess(res["equity_curve"][-1]["net_equity"], res["equity_curve"][-1]["gross_equity"])
        # cash accounting: nothing appears from nowhere
        cash = self.config.initial_cash
        for f in res["fills"]:
            if f["side"] == "buy":
                cash -= f["reference_notional"] + f["commission"]
            else:
                cash += f["reference_notional"] - f["commission"] - f["tax"]
        self.assertAlmostEqual(cash, res["ending_cash"], places=4)
        self.assertGreaterEqual(res["ending_cash"], 0)

    def test_test_window_data_cannot_change_the_boundary_target(self):
        before = self.run_fold()["boundary_signal"]
        with self.engine.begin() as c:
            c.exec_driver_sql("UPDATE daily_quotes SET open=open*5, close=close*5, high=high*5, low=low*5 "
                              "WHERE date > '2025-02-14' AND stock_id='1103'")
            c.exec_driver_sql("UPDATE daily_quotes SET open=open*.2, close=close*.2 "
                              "WHERE date > '2025-02-14' AND stock_id='1101'")
        after = self.run_fold()["boundary_signal"]
        self.assertEqual((before.candidates, dict(before.scores)), (after.candidates, dict(after.scores)))

    def test_only_cash_or_prior_oos_terminal_states_are_valid(self):
        fold = {**self.fold("2025-02-14", "2025-02-17"), "index": 1}
        prev = {"index": 0, "test_end": "2025-02-14"}
        wf_oos.validate_initial_state(None, fold)
        wf_oos.validate_initial_state({"source": "cash"}, fold)
        wf_oos.validate_initial_state({"source": "prior_oos_terminal", "from_fold": 0, "as_of": "2025-02-14"},
                                      fold, prev)
        with self.assertRaisesRegex(ValueError, "TRAINING_REPLAY_STATE_FORBIDDEN"):
            wf_oos.validate_initial_state({"source": "training_replay"}, fold, prev)
        for bad in ({"source": "prior_oos_terminal", "from_fold": 0, "as_of": "2025-01-01"},
                    {"source": "prior_oos_terminal", "from_fold": 3, "as_of": "2025-02-14"},
                    {"source": "mystery"}):
            with self.assertRaises(ValueError):
                wf_oos.validate_initial_state(bad, fold, prev)
        with self.assertRaises(ValueError):   # no previous OOS fold
            wf_oos.validate_initial_state({"source": "prior_oos_terminal", "from_fold": 0,
                                           "as_of": "2025-02-14"}, {**fold, "index": 1}, None)
        with self.assertRaisesRegex(ValueError, "independent-fold only"):
            wf_oos.run_oos_fold(self.panel(), self.rule(), self.config, fold,
                                initial_state={"source": "prior_oos_terminal", "from_fold": 0,
                                               "as_of": "2025-02-14"})
        with self.assertRaisesRegex(ValueError, "TRAINING_REPLAY"):
            wf_oos.run_oos_fold(self.panel(), self.rule(), self.config, fold,
                                initial_state={"source": "training_replay"})

    def test_gap_and_non_adjacent_boundary_are_refused(self):
        with self.assertRaisesRegex(ValueError, "gap_sessions"):
            wf_oos.run_oos_fold(self.panel(), self.rule(), self.config,
                                {**self.fold("2025-02-12", "2025-02-14"), "gap_sessions": 1})
        with self.assertRaisesRegex(ValueError, "immediately before"):
            self.run_fold("2025-02-12", "2025-02-14")

    def test_contract_is_published_once(self):
        c = wf_oos.CONTRACT
        self.assertEqual(c["independent_fold"]["initial_state"], "cash")
        self.assertNotIn("stitched_oos", c)          # single source of truth: walkforward.contracts
        from app.services.walkforward import contracts
        got = contracts.get()
        self.assertTrue(got["stitched_oos"]["implemented"])
        self.assertIn("training_replay", got["stitched_oos"]["forbidden_state_sources"])


# ── boundaries that must not regress ────────────────────
class BoundaryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/b.db")
        migrate(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_publication_visible_only_strictly_after_publish_date(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": "A", "name": "A", "type": "stock", "industry": "X"}])
            upsert_many(c, t.trading_calendar, [{"date": d} for d in dates])
            upsert_many(c, t.daily_quotes, [{"stock_id": "A", "date": d, "open": 10, "close": 10} for d in dates])
            upsert_many(c, t.month_revenue, [{"stock_id": "A", "ym": "2024-12", "revenue": 1,
                                              "publish_date": "2025-01-03", "publish_source": "actual"}])
            upsert_many(c, t.financials, [{"stock_id": "A", "quarter": "2024Q4", "eps": 1,
                                           "publish_date": "2025-01-03", "publish_source": "assumed_deadline"}])
        strict = build_panel(dates[0], dates[-1], ["A"], publication_mode="strict",
                             db_engine=self.engine, benchmark_id=None)
        self.assertEqual(strict.days[1].publications, ())                      # not on publish day
        self.assertEqual([p.dataset for p in strict.days[2].publications], ["month_revenue"])
        assumed = build_panel(dates[0], dates[-1], ["A"], publication_mode="assumed",
                              db_engine=self.engine, benchmark_id=None)
        self.assertEqual({p.dataset for p in assumed.days[2].publications}, {"month_revenue", "financials"})

    def test_adjusted_price_history_is_point_in_time(self):
        d = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"]
        closes = [100, 100, 95, 95, 47.5, 47.5]

        def series(actions):
            bars = {x: {"A": Bar("A", x, open=c, close=c, amount=1e8)} for x, c in zip(d, closes)}
            out, _ = adjust_bars(bars, actions, d)
            return [out[x]["A"].adj_close for x in d]
        early = {"stock_id": "A", "date": d[2], "kind": "dividend_cash", "ratio": .95}
        late = {"stock_id": "A", "date": d[4], "kind": "split", "ratio": .5}
        self.assertEqual(series([early, late])[:4], series([early])[:4])   # a later event never rewrites history

    def test_walkforward_folds_use_only_past_for_train(self):
        days = _sessions("2022-11-01", "2026-09-18")
        for f in wf.generate_folds("2022-11-01", "2026-09-18", days)["folds"]:
            self.assertLess(f["train_end"], f["test_start"])


if __name__ == "__main__":
    unittest.main()
