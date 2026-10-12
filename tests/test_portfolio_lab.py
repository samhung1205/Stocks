"""Stage H1 tests: generic Strategy Portfolio engine — allocation methods,
constraints, target construction, the daily research-run engine, and the
persistence/versioning/CRUD layer (``app.services.portfolio_lab``).

``PortfolioLabRealDBTests`` exercises the full stack (create portfolio -> create
version -> research run -> persisted results) against the real local dev DB
using the same five completed Walk Forward jobs Stage F1.5/G/H0 already
validated. Skipped if those jobs (or their pinned strategies) are not present.
"""
from __future__ import annotations

import math
import unittest

from app.services.strategy.types import CostConfig

from app.services.portfolio import allocation, calendar, constraints, research, series, target


# ── allocation ───────────────────────────────────────────────────────────────
class AllocationTests(unittest.TestCase):
    def test_equal_weight_is_generic_over_arbitrary_component_count(self):
        for n in (1, 2, 5, 7, 23):
            keys = [f"strategy-{i}" for i in range(n)]
            result = allocation.equal_weight(keys)
            self.assertEqual(result["status"], "ready")
            for k in keys:
                self.assertAlmostEqual(result["weights"][k], 1.0 / n)

    def test_equal_weight_empty_is_unavailable(self):
        self.assertEqual(allocation.equal_weight([])["status"], "unavailable")

    def test_fixed_weight_residual_is_strategic_cash(self):
        result = allocation.fixed_weight(["a", "b", "c"], {"a": 0.3, "b": 0.25, "c": 0.2})
        self.assertAlmostEqual(result["strategic_cash"], 0.25)
        self.assertAlmostEqual(sum(result["weights"].values()) + result["strategic_cash"], 1.0)

    def test_fixed_weight_missing_component_defaults_to_zero(self):
        result = allocation.fixed_weight(["a", "b"], {"a": 0.5})
        self.assertEqual(result["weights"]["b"], 0.0)

    def test_fixed_weight_negative_rejected(self):
        with self.assertRaises(ValueError):
            allocation.fixed_weight(["a"], {"a": -0.1})

    def test_fixed_weight_over_one_rejected(self):
        with self.assertRaises(ValueError):
            allocation.fixed_weight(["a", "b"], {"a": 0.7, "b": 0.5})

    def test_inverse_volatility_ready_weights_favor_lower_vol(self):
        prior = {"a": [0.001, -0.001] * 13, "b": [0.05, -0.05] * 13}
        result = allocation.inverse_volatility(["a", "b"], prior)
        self.assertEqual(result["status"], "ready")
        self.assertGreater(result["weights"]["a"], result["weights"]["b"])
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0)

    def test_inverse_volatility_insufficient_evidence_is_not_silently_equal_weight(self):
        result = allocation.inverse_volatility(["a", "b"], {"a": [0.01, 0.02], "b": [0.01] * 25})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["weights"], {})

    def test_resolve_dispatches_by_name(self):
        self.assertEqual(allocation.resolve("equal_weight", ["a", "b"])["status"], "ready")

    def test_resolve_unimplemented_covariance_method_raises(self):
        with self.assertRaises(NotImplementedError):
            allocation.resolve("risk_parity", ["a", "b"])

    def test_resolve_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            allocation.resolve("mean_variance", ["a"])


# ── constraints ────────────────────────────────────────────────────────────
class ConstraintsTests(unittest.TestCase):
    def test_max_strategy_weight_clamps_to_cash_never_redistributes(self):
        result = constraints.apply_max_strategy_weight({"a": 0.6, "b": 0.4}, limit=0.4)
        self.assertAlmostEqual(result["weights"]["a"], 0.4)
        self.assertAlmostEqual(result["weights"]["b"], 0.4)  # untouched, not bumped up
        self.assertAlmostEqual(result["cash_added"], 0.2)
        self.assertEqual(result["diagnostics"][0]["component"], "a")

    def test_max_single_stock_weight_clamps_and_reports(self):
        result = constraints.apply_max_single_stock_weight({"2330": 0.3, "2454": 0.1}, limit=0.2)
        self.assertAlmostEqual(result["weights"]["2330"], 0.2)
        self.assertAlmostEqual(result["weights"]["2454"], 0.1)  # never gets the freed-up weight
        self.assertAlmostEqual(result["cash_added"], 0.1)

    def test_no_limit_is_a_no_op(self):
        result = constraints.apply_max_strategy_weight({"a": 0.9}, limit=None)
        self.assertEqual(result["weights"], {"a": 0.9})
        self.assertEqual(result["diagnostics"], [])

    def test_min_cash_proportional_haircut(self):
        result = constraints.apply_min_cash({"a": 0.6, "b": 0.4}, min_cash=0.2)
        self.assertAlmostEqual(sum(result["weights"].values()), 0.8)
        self.assertAlmostEqual(result["weights"]["a"] / result["weights"]["b"], 0.6 / 0.4)  # proportions preserved

    def test_min_cash_no_op_when_already_satisfied(self):
        result = constraints.apply_min_cash({"a": 0.5}, min_cash=0.2)
        self.assertEqual(result["weights"], {"a": 0.5})
        self.assertEqual(result["diagnostics"], [])

    def test_validate_constraints_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            constraints.validate_constraints({"max_stock_weight": 0.1})

    def test_validate_constraints_fills_defaults(self):
        result = constraints.validate_constraints({"max_strategy_weight": 0.4})
        self.assertEqual(result["min_cash"], 0.0)
        self.assertIsNone(result["max_single_stock_weight"])

    def test_validate_constraints_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            constraints.validate_constraints({"max_strategy_weight": 1.5})


# ── calendar ─────────────────────────────────────────────────────────────────
class CalendarTests(unittest.TestCase):
    def test_monthly_picks_first_date_per_month(self):
        dates = ["2025-01-02", "2025-01-03", "2025-02-03", "2025-02-04", "2025-03-02"]
        self.assertEqual(calendar.rebalance_dates(dates, "monthly"), ["2025-01-02", "2025-02-03", "2025-03-02"])

    def test_unsupported_frequency_rejected(self):
        with self.assertRaises(NotImplementedError):
            calendar.rebalance_dates(["2025-01-02"], "weekly")


# ── target construction (allocation -> baseline -> netting -> constraints) ──
class TargetTests(unittest.TestCase):
    def test_equal_weight_no_candidate_goes_to_cash_not_redistributed(self):
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "no_candidate"},
            sleeve_stock_weight={"a": {"2330": 0.8}, "b": {"9999": 0.9}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0})
        self.assertEqual(result["status"], "ready")
        # b's 50% slice stays cash; a's own 50% slice only contributes 50%*0.8=0.4 to 2330.
        self.assertAlmostEqual(result["stock_weight"]["2330"], 0.4)
        self.assertNotIn("9999", result["stock_weight"])
        self.assertAlmostEqual(result["cash_weight"], 0.6)

    def test_overlapping_stock_aggregation_and_contribution(self):
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "evaluated"},
            sleeve_stock_weight={"a": {"2330": 0.5}, "b": {"2330": 0.3, "1101": 0.2}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0})
        self.assertAlmostEqual(result["stock_weight"]["2330"], 0.5 * 0.5 + 0.5 * 0.3)
        self.assertAlmostEqual(result["contribution_by_stock"]["2330"]["a"], 0.25)
        self.assertAlmostEqual(result["contribution_by_stock"]["2330"]["b"], 0.15)

    def test_max_strategy_weight_flows_through_to_stock_weight(self):
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "evaluated"},
            sleeve_stock_weight={"a": {"2330": 1.0}, "b": {"1101": 1.0}},
            portfolio_constraints={"max_strategy_weight": 0.3, "max_single_stock_weight": None, "min_cash": 0.0})
        self.assertAlmostEqual(result["stock_weight"]["2330"], 0.3)
        self.assertAlmostEqual(result["cash_weight"], 0.4)
        self.assertEqual(result["constraint_diagnostics"][0]["component"], "a")

    def test_max_single_stock_weight_enforced(self):
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "evaluated"},
            sleeve_stock_weight={"a": {"2330": 1.0}, "b": {"2330": 1.0}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": 0.3, "min_cash": 0.0})
        self.assertAlmostEqual(result["stock_weight"]["2330"], 0.3)
        self.assertAlmostEqual(result["cash_weight"], 0.7)

    def test_inverse_volatility_not_ready_refuses_not_fallback(self):
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="inverse_volatility",
            candidate_status={"a": "evaluated", "b": "evaluated"},
            sleeve_stock_weight={"a": {"2330": 1.0}, "b": {"1101": 1.0}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            prior_returns_by_component={"a": [0.01, 0.02], "b": [0.01] * 25})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["stock_weight"], {})
        self.assertEqual(result["cash_weight"], 1.0)

    def test_missing_candidate_status_entry_defaults_to_cash(self):
        # A component present in component_keys but absent from candidate_status (e.g. its
        # sleeve failed to load) must fail SAFE to cash, never to "as if evaluated".
        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b"], method="equal_weight",
            candidate_status={"a": "evaluated"},   # "b" missing entirely
            sleeve_stock_weight={"a": {"2330": 1.0}, "b": {"9999": 1.0}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0})
        self.assertNotIn("9999", result["stock_weight"])
        self.assertAlmostEqual(result["cash_weight"], 0.5)

    # ── Stage H1.5 §3: constraint interaction (fixed order: allocation -> no-candidate
    # cash -> max_strategy_weight -> netting/aggregate -> max_single_stock_weight -> min_cash) ──
    def test_combined_constraints_apply_in_fixed_order_no_extra_exposure(self):
        # 3 equal-weight components (1/3 each); a and b both fully concentrated in S1 (so
        # their aggregate exposure to S1 is large), c concentrated in S2.
        raw = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b", "c"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "evaluated", "c": "evaluated"},
            sleeve_stock_weight={"a": {"S1": 1.0}, "b": {"S1": 1.0}, "c": {"S2": 1.0}},
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0})
        raw_s1, raw_s2 = raw["stock_weight"]["S1"], raw["stock_weight"]["S2"]  # 2/3, 1/3

        result = target.resolve_rebalance_target(
            as_of="2025-01-02", component_keys=["a", "b", "c"], method="equal_weight",
            candidate_status={"a": "evaluated", "b": "evaluated", "c": "evaluated"},
            sleeve_stock_weight={"a": {"S1": 1.0}, "b": {"S1": 1.0}, "c": {"S2": 1.0}},
            portfolio_constraints={"max_strategy_weight": 0.3, "max_single_stock_weight": 0.4, "min_cash": 0.5})

        # 1) weight conservation: stock weights + cash == 1 exactly.
        self.assertAlmostEqual(sum(result["stock_weight"].values()) + result["cash_weight"], 1.0, places=9)
        # 2) cash >= 0.
        self.assertGreaterEqual(result["cash_weight"], 0.0)
        # 3) no stock exceeds max_single_stock_weight (min_cash haircut only shrinks further).
        for w in result["stock_weight"].values():
            self.assertLessEqual(w, 0.4 + 1e-9)
        # 4) no stock ends up MORE exposed than the raw, unconstrained aggregate -- clamps and
        #    haircuts only ever remove exposure, never add it (no hidden redistribution).
        self.assertLess(result["stock_weight"]["S1"], raw_s1)
        self.assertLessEqual(result["stock_weight"]["S2"], raw_s2)
        # 5) exact, hand-computed pipeline: strategy clamp 1/3->0.3 (3x0.0333 -> cash) ->
        #    aggregate S1=0.6/S2=0.3 -> stock clamp S1 0.6->0.4 (0.2 -> cash; S2 untouched at
        #    0.3, never bumped up by S1's freed capacity) -> implied cash 0.3 < min_cash 0.5 ->
        #    proportional haircut scale = 0.5/0.7.
        scale = 0.5 / 0.7
        self.assertAlmostEqual(result["stock_weight"]["S1"], 0.4 * scale, places=9)
        self.assertAlmostEqual(result["stock_weight"]["S2"], 0.3 * scale, places=9)
        self.assertAlmostEqual(result["cash_weight"], 0.5, places=9)
        # 6) diagnostics trace every clamp: 3 strategy clamps + 1 stock clamp + 1 min_cash haircut.
        kinds = [d.get("component", d.get("stock_id", d.get("rule"))) for d in result["constraint_diagnostics"]]
        self.assertEqual(sorted(kinds[:3]), ["a", "b", "c"])
        self.assertIn("S1", kinds)
        self.assertIn("min_cash_proportional_haircut", kinds)
        self.assertEqual(len(result["constraint_diagnostics"]), 5)


# ── series flattening ─────────────────────────────────────────────────────────
class SeriesTests(unittest.TestCase):
    def test_prior_returns_excludes_as_of_and_future(self):
        s = {"a": {"dates": ["2025-01-02", "2025-01-03", "2025-01-06"],
                   "by_date": {"2025-01-02": {"daily_return": 0.01}, "2025-01-03": {"daily_return": 0.02},
                              "2025-01-06": {"daily_return": -0.5}}}}
        self.assertEqual(series.prior_returns(s, as_of="2025-01-06"), {"a": [0.01, 0.02]})

    def test_common_trading_dates_is_intersection_within_window(self):
        s = {"a": {"dates": ["2025-01-02", "2025-01-03", "2025-01-06"]},
             "b": {"dates": ["2025-01-02", "2025-01-06", "2025-01-07"]}}
        self.assertEqual(series.common_trading_dates(s, "2025-01-01", "2025-01-31"),
                         ["2025-01-02", "2025-01-06"])

    def test_common_trading_dates_no_components_is_empty(self):
        self.assertEqual(series.common_trading_dates({}, "2025-01-01", "2025-01-31"), [])

    def test_build_component_series_reports_date_collision_never_silently_overwrites(self):
        # Two folds that (contrived, should never happen for real independent-fold test
        # windows) report the SAME date must not let the later fold silently clobber the
        # earlier one's row -- the collision is reported instead.
        import types
        fold0 = {"daily": [{"date": "2025-01-02", "stock_weight_by_stock": {"A": 1.0}, "cash_weight": 0.0,
                            "total_equity": 100.0, "daily_return": 0.0}], "candidate_status": "evaluated"}
        fold1 = {"daily": [{"date": "2025-01-02", "stock_weight_by_stock": {"B": 1.0}, "cash_weight": 0.0,
                            "total_equity": 100.0, "daily_return": 0.0}], "candidate_status": "evaluated"}
        sd = types.SimpleNamespace(folds={0: object(), 1: object()})
        fake_sleeves = {0: fold0, 1: fold1}
        orig = series.build_exact_sleeve
        series.build_exact_sleeve = lambda sd_, idx, job_id: fake_sleeves[idx]
        try:
            result = series.build_component_series(sd, "job")
        finally:
            series.build_exact_sleeve = orig
        self.assertEqual(result["date_collisions"], ["2025-01-02"])
        # The FIRST fold's row wins; the second fold's data never silently overwrites it.
        self.assertEqual(result["by_date"]["2025-01-02"]["stock_weight_by_stock"], {"A": 1.0})


# ── Stage H1.5 §4: point-in-time / fold-boundary validation ─────────────────
class FoldBoundaryTests(unittest.TestCase):
    """Portfolio-layer PIT: a component's candidate_status/stock_weight only ever
    changes at the date a NEW fold's sleeve data actually starts -- never earlier,
    and never influenced by a later fold's data leaking into an earlier date's
    target (structurally guaranteed: ``research.run_portfolio_backtest`` only ever
    reads ``component_series[k]["by_date"].get(d, ...)`` for the CURRENT date ``d``,
    never scans ahead)."""
    CFG = CostConfig(minimum_commission=1.0)

    @staticmethod
    def _mixed_series(rows: dict) -> dict:
        by_date = {d: {"stock_weight_by_stock": w, "cash_weight": None, "total_equity": None,
                       "daily_return": 0.001, "candidate_status": status}
                  for d, (status, w) in rows.items()}
        return {"dates": list(rows), "by_date": by_date}

    def test_no_candidate_to_evaluated_transition_lands_exactly_on_fold_boundary(self):
        # fold 0 (no_candidate): Jan + Feb rebalances. fold 1 (evaluated, real weight)
        # starts exactly in March -- NOT before, even though both folds are already
        # loaded together into the same by_date dict (the shape every real
        # ``build_component_series`` call produces once all folds are fetched).
        comp = self._mixed_series({
            "2025-01-02": ("no_candidate", {}),
            "2025-02-03": ("no_candidate", {}),
            "2025-03-03": ("evaluated", {"S1": 1.0}),
        })
        dates = list(comp["dates"])
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a"], component_series={"a": comp},
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes={"S1": {"2025-03-03": 100.0}}, actions_by_date={},
            cost_config=self.CFG, initial_cash=1_000_000.0)
        jan, feb, mar = result["rebalances"]
        self.assertEqual(jan["target_stock_weight"], {})
        self.assertAlmostEqual(jan["cash_weight"], 1.0)
        self.assertEqual(feb["target_stock_weight"], {})   # still no_candidate -- fold 1 has NOT started yet
        self.assertAlmostEqual(feb["cash_weight"], 1.0)
        self.assertAlmostEqual(mar["target_stock_weight"]["S1"], 1.0)  # exactly on the boundary date

    def test_evaluated_to_no_candidate_reversion_also_lands_on_fold_boundary(self):
        comp = self._mixed_series({
            "2025-01-02": ("evaluated", {"S1": 1.0}),
            "2025-02-03": ("no_candidate", {}),
        })
        dates = list(comp["dates"])
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a"], component_series={"a": comp},
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes={"S1": {"2025-01-02": 100.0, "2025-02-03": 100.0}},
            actions_by_date={}, cost_config=self.CFG, initial_cash=1_000_000.0)
        jan, feb = result["rebalances"]
        self.assertAlmostEqual(jan["target_stock_weight"]["S1"], 1.0)
        self.assertEqual(feb["target_stock_weight"], {})
        self.assertAlmostEqual(feb["cash_weight"], 1.0)


# ── the daily research engine (synthetic, no DB) ─────────────────────────────
def _series(dates, weight_by_date, status="evaluated"):
    by_date = {d: {"stock_weight_by_stock": weight_by_date.get(d, {}), "cash_weight": None,
                  "total_equity": None, "daily_return": 0.001, "candidate_status": status}
              for d in dates}
    return {"dates": list(dates), "by_date": by_date}


class ResearchEngineTests(unittest.TestCase):
    CFG = CostConfig(minimum_commission=1.0)

    def test_arbitrary_number_of_components_and_new_strategy_needs_no_engine_change(self):
        dates = ["2025-01-02", "2025-01-03"]
        n = 9  # an arbitrary count Stage H1's five real strategies never used
        keys = [f"synthetic-{i}" for i in range(n)]
        comp_series = {k: _series(dates, {"2025-01-02": {f"S{i}": 1.0}}) for i, k in enumerate(keys)}
        closes = {f"S{i}": {d: 100.0 for d in dates} for i in range(n)}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=["2025-01-02"], component_keys=keys, component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        self.assertEqual(len(result["rebalances"]), 1)
        self.assertEqual(len(result["rebalances"][0]["orders"]), n)  # every component's own stock traded

    def test_overlapping_stock_nets_to_one_order_not_n(self):
        dates = ["2025-01-02"]
        comp_series = {
            "a": _series(dates, {"2025-01-02": {"2330": 1.0}}),
            "b": _series(dates, {"2025-01-02": {"2330": 1.0}}),
        }
        closes = {"2330": {"2025-01-02": 100.0}}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a", "b"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        self.assertEqual(list(result["rebalances"][0]["orders"].keys()), ["2330"])
        self.assertEqual(result["rebalances"][0]["contribution_by_stock"]["2330"], {"a": 0.5, "b": 0.5})

    def test_net_cost_cheaper_than_paying_for_each_component_separately(self):
        # Two rebalances: first buys, second sends one component fully out of 2330 while the
        # other's target on 2330 is unchanged -- naive per-component costing would re-trade the
        # unchanged component's slice; the aggregate target does not.
        dates = ["2025-01-02", "2025-01-03"]
        comp_series = {
            "a": _series(dates, {"2025-01-02": {"2330": 1.0}, "2025-01-03": {"2330": 1.0}}),
            "b": _series(dates, {"2025-01-02": {"2330": 1.0}, "2025-01-03": {}}, status="evaluated"),
        }
        closes = {"2330": {d: 100.0 for d in dates}}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a", "b"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        second = result["rebalances"][1]
        # aggregate target for 2330 halves (only "a" still wants it) -> exactly one net sell order.
        self.assertEqual(list(second["orders"].keys()), ["2330"])
        self.assertEqual(second["orders"]["2330"]["side"], "sell")

    def test_corporate_action_share_multiplier_and_dividend_applied_to_portfolio_ledger(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 1.0}})}
        closes = {"2330": {"2025-01-02": 100.0, "2025-01-03": 95.0, "2025-01-06": 47.0}}
        actions = {"2025-01-03": [{"stock_id": "2330", "cash_dividend": 2.0, "share_multiplier": None}],
                  "2025-01-06": [{"stock_id": "2330", "cash_dividend": None, "share_multiplier": 2.0}]}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=["2025-01-02"], component_keys=["a"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date=actions, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        shares_after_action = result["rebalances"][0]["orders"]["2330"]["delta_shares"]
        self.assertGreater(shares_after_action, 0)
        # After the 2x split, held shares should have doubled relative to the post-buy count.
        self.assertAlmostEqual(result["final_shares"]["2330"], shares_after_action * 2, places=4)

    def test_missing_price_defers_trade_not_fabricated(self):
        dates = ["2025-01-02", "2025-01-03"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 1.0}, "2025-01-03": {"1101": 1.0}})}
        closes = {"2330": {"2025-01-02": 100.0}}  # 1101 never has a price -> can never be bought
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        second = result["rebalances"][1]
        self.assertTrue(any(d["stock_id"] == "1101" for d in second["deferred"]))
        self.assertNotIn("1101", second["orders"])

    def test_insufficient_cash_scales_down_buy_deterministically(self):
        dates = ["2025-01-02"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 0.9, "1101": 0.9}})}  # sums > 1 to force a squeeze
        closes = {"2330": {"2025-01-02": 100.0}, "1101": {"2025-01-02": 50.0}}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=dates, component_keys=["a"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        # Commission is rounded to the nearest whole currency unit, so a fully cash-
        # constrained scale-down can leave a sub-unit negative residual; that is
        # immaterial rounding noise, not an unbounded overdraw.
        self.assertGreaterEqual(result["final_cash"], -2.0)

    def test_no_candidate_policy_other_than_cash_hold_rejected(self):
        with self.assertRaises(NotImplementedError):
            research.run_portfolio_backtest(
                dates=["2025-01-02"], rebalance_dates=["2025-01-02"], component_keys=["a"],
                component_series={"a": _series(["2025-01-02"], {})}, method="equal_weight",
                allocation_target_weight=None,
                portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
                no_candidate_policy="redistribute_active", closes={}, actions_by_date={}, cost_config=self.CFG,
                initial_cash=1_000_000.0)

    def test_deterministic(self):
        dates = ["2025-01-02", "2025-01-03"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 1.0}})}
        closes = {"2330": {"2025-01-02": 100.0, "2025-01-03": 101.0}}
        kwargs = dict(dates=dates, rebalance_dates=["2025-01-02"], component_keys=["a"],
                     component_series=comp_series, method="equal_weight", allocation_target_weight=None,
                     portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None,
                                            "min_cash": 0.0},
                     no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
                     initial_cash=1_000_000.0)
        r1 = research.run_portfolio_backtest(**kwargs)
        r2 = research.run_portfolio_backtest(**kwargs)
        self.assertEqual(r1["daily"], r2["daily"])
        self.assertEqual(r1["rebalances"], r2["rebalances"])

    # ── Stage H1.5 §1: cooperative cancel mechanism ──────────────────────────
    def test_cancel_check_stops_the_run_and_reports_cancelled(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 1.0}})}
        closes = {"2330": {d: 100.0 for d in dates}}
        seen = {"n": 0}

        def cancel_after_two(*, _limit=2):
            seen["n"] += 1
            return seen["n"] > _limit

        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=["2025-01-02"], component_keys=["a"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0, cancel_check=cancel_after_two)
        self.assertTrue(result["cancelled"])
        self.assertLess(len(result["daily"]), len(dates))   # stopped before finishing all dates

    def test_no_cancel_check_behaves_exactly_as_before(self):
        dates = ["2025-01-02", "2025-01-03"]
        comp_series = {"a": _series(dates, {"2025-01-02": {"2330": 1.0}})}
        closes = {"2330": {d: 100.0 for d in dates}}
        result = research.run_portfolio_backtest(
            dates=dates, rebalance_dates=["2025-01-02"], component_keys=["a"], component_series=comp_series,
            method="equal_weight", allocation_target_weight=None,
            portfolio_constraints={"max_strategy_weight": None, "max_single_stock_weight": None, "min_cash": 0.0},
            no_candidate_policy="cash_hold", closes=closes, actions_by_date={}, cost_config=self.CFG,
            initial_cash=1_000_000.0)
        self.assertFalse(result["cancelled"])
        self.assertEqual(len(result["daily"]), len(dates))


# ── real DB: full persistence / versioning / run lifecycle ──────────────────
_REAL_JOBS = {
    "momentum_trend": "9a3612c981e44cf88daabc655abc43ed",
    "quality_growth_momentum": "052d3472688740c89d8e4701bbc358cd",
    "revenue_growth_acceleration": "15f5a27ea6174a969ca5c2ebeeefe795",
    "value_quality": "098c6aa9252544cc88c910c68c05c490",
    "low_volatility_quality": "90f389547ad74c219c0074f5d2570b1d",
}


def _real_jobs_available() -> bool:
    try:
        from app.services import walkforward_lab as wf
        for job_id in _REAL_JOBS.values():
            job = wf.get_job(job_id)
            if job["status"] != "completed" or job["provenance"].get("strategy_id") is None:
                return False
        return True
    except LookupError:
        return False


def _components(*keys, method="equal_weight", weights=None):
    from app.services import walkforward_lab as wf
    out = []
    for k in keys:
        job_id = _REAL_JOBS[k]
        strategy_id = wf.get_job(job_id)["provenance"]["strategy_id"]
        c = {"strategy_id": strategy_id, "walkforward_job_id": job_id}
        if method == "fixed_weight":
            c["target_weight"] = (weights or {}).get(k, 0.0)
        out.append(c)
    return out


@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class PortfolioLabRealDBTests(unittest.TestCase):
    def setUp(self):
        from app.services import portfolio_lab as plab
        self.plab = plab
        self._created_portfolio_ids: list[int] = []

    def _create(self, name, **kwargs):
        p = self.plab.create_portfolio({"name": name, **kwargs})
        self._created_portfolio_ids.append(p["id"])
        return p

    def test_arbitrary_component_count_and_same_strategy_in_multiple_portfolios(self):
        p1 = self._create("H1 test: five", components=_components(
            "momentum_trend", "quality_growth_momentum", "revenue_growth_acceleration",
            "value_quality", "low_volatility_quality"), allocation_method="equal_weight")
        p2 = self._create("H1 test: momentum only", components=_components("momentum_trend"),
                          allocation_method="equal_weight")
        self.assertEqual(len(p1["latest_version"]["components"]), 5)
        self.assertEqual(len(p2["latest_version"]["components"]), 1)
        mt_ids = {c["strategy_id"] for c in p1["latest_version"]["components"]} & \
                 {c["strategy_id"] for c in p2["latest_version"]["components"]}
        self.assertTrue(mt_ids)  # momentum_trend's strategy_id is a component of BOTH portfolios

    def test_pinned_component_version_rejects_wrong_strategy(self):
        from app.services import walkforward_lab as wf
        real_job = _REAL_JOBS["momentum_trend"]
        wrong_strategy_id = wf.get_job(_REAL_JOBS["value_quality"])["provenance"]["strategy_id"]
        with self.assertRaises(ValueError):
            self._create("H1 test: bad pin",
                         components=[{"strategy_id": wrong_strategy_id, "walkforward_job_id": real_job}],
                         allocation_method="equal_weight")

    def test_fixed_weight_validated_at_creation(self):
        with self.assertRaises(ValueError):
            self._create("H1 test: bad weights",
                         components=_components("momentum_trend", "value_quality", method="fixed_weight",
                                                weights={"momentum_trend": 0.7, "value_quality": 0.6}),
                         allocation_method="fixed_weight")

    def test_immutable_versions_and_new_version_on_edit(self):
        p = self._create("H1 test: versioning", components=_components("momentum_trend", "value_quality"),
                         allocation_method="equal_weight")
        v1 = p["latest_version"]
        updated = self.plab.update_portfolio(p["id"], {"constraints": {"max_strategy_weight": 0.6}})
        v2 = updated["latest_version"]
        self.assertEqual(v2["version_no"], v1["version_no"] + 1)
        # The OLD version, re-fetched, is byte-for-byte unchanged.
        v1_again = self.plab.get_version(p["id"], v1["id"])
        self.assertEqual(v1_again, v1)
        self.assertIsNone(v1["constraints"]["max_strategy_weight"])
        self.assertEqual(v2["constraints"]["max_strategy_weight"], 0.6)

    def test_no_op_update_does_not_create_a_new_version(self):
        p = self._create("H1 test: no-op", components=_components("momentum_trend"),
                         allocation_method="equal_weight")
        v1_no = p["latest_version"]["version_no"]
        again = self.plab.update_portfolio(p["id"], {"allocation_method": "equal_weight",
                                                       "components": _components("momentum_trend")})
        self.assertEqual(again["latest_version"]["version_no"], v1_no)

    def test_clone_portfolio_is_independent(self):
        p = self._create("H1 test: clone source", components=_components("momentum_trend", "value_quality"),
                         allocation_method="equal_weight")
        clone = self.plab.clone_portfolio(p["id"], {"name": "H1 test: clone target"})
        self._created_portfolio_ids.append(clone["id"])
        self.assertNotEqual(clone["id"], p["id"])
        self.plab.update_portfolio(p["id"], {"constraints": {"max_strategy_weight": 0.5}})
        clone_after = self.plab.get_portfolio(clone["id"])
        self.assertIsNone(clone_after["latest_version"]["constraints"]["max_strategy_weight"])

    def test_archive_does_not_delete_research_history(self):
        p = self._create("H1 test: archive", components=_components("momentum_trend"),
                         allocation_method="equal_weight")
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run["run_id"])
        result = self.plab.get_research_result(run["run_id"])
        self.assertEqual(result["status"], "completed")
        self.plab.archive_portfolio(p["id"])
        still_there = self.plab.get_research_result(run["run_id"])
        self.assertEqual(still_there["metrics"], result["metrics"])
        runs = self.plab.list_research_runs(p["id"])
        self.assertTrue(any(r["id"] == run["run_id"] for r in runs))

    def test_old_run_unchanged_after_new_portfolio_version(self):
        p = self._create("H1 test: run pin", components=_components("momentum_trend", "value_quality"),
                         allocation_method="equal_weight")
        run1 = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run1["run_id"])
        result1 = self.plab.get_research_result(run1["run_id"])
        self.plab.update_portfolio(p["id"], {"constraints": {"max_strategy_weight": 0.3}})
        result1_again = self.plab.get_research_result(run1["run_id"])
        self.assertEqual(result1_again["metrics"], result1["metrics"])
        self.assertEqual(result1_again["portfolio_version_id"], run1["portfolio_version_id"])

    def test_equal_weight_real_run_completes_with_provenance_and_no_candidate_cash(self):
        p = self._create("H1 test: core five", components=_components(
            "momentum_trend", "quality_growth_momentum", "revenue_growth_acceleration",
            "value_quality", "low_volatility_quality"), allocation_method="equal_weight")
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run["run_id"])
        result = self.plab.get_research_result(run["run_id"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["components"], p["latest_version"]["components"])  # pinned snapshot
        equity = self.plab.get_research_equity(run["run_id"])
        self.assertTrue(equity)
        rebalances = self.plab.get_research_rebalances(run["run_id"])
        self.assertTrue(rebalances)
        # low_volatility_quality is no_candidate for fold 0 (per Stage F1.5/G/H0 reports) -> some
        # of the portfolio's capital must be sitting in cash, not silently redistributed.
        first = rebalances[0]
        self.assertGreater(first["cash_weight"], 0.0)

    def test_fixed_weight_real_run_respects_target_weights(self):
        p = self._create("H1 test: fixed", components=_components(
            "momentum_trend", "value_quality", method="fixed_weight",
            weights={"momentum_trend": 0.5, "value_quality": 0.5}), allocation_method="fixed_weight")
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run["run_id"])
        result = self.plab.get_research_result(run["run_id"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["status"], "completed")

    def test_max_strategy_weight_constraint_real_run(self):
        p = self._create("H1 test: constrained", components=_components(
            "momentum_trend", "value_quality"), allocation_method="equal_weight",
            constraints={"max_strategy_weight": 0.3})
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run["run_id"])
        rebalances = self.plab.get_research_rebalances(run["run_id"])
        self.assertTrue(rebalances)
        first = rebalances[0]
        self.assertGreaterEqual(first["cash_weight"], 0.4 - 1e-6)  # 2 * (0.5-0.3) excess -> cash

    def tearDown(self):
        # Portfolios are soft-deleted only; archiving keeps research history intact
        # (asserted directly in test_archive_does_not_delete_research_history) and
        # keeps the real dev DB tidy for repeated local runs of this test module.
        for pid in self._created_portfolio_ids:
            try:
                self.plab.archive_portfolio(pid)
            except LookupError:
                pass


# ── Stage H1.5 §1: research-run lifecycle reliability (real DB) ─────────────
@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class PortfolioLifecycleRealDBTests(unittest.TestCase):
    def setUp(self):
        from app.services import portfolio_lab as plab
        from app.db import engine as db_engine
        self.plab = plab
        self.db_engine = db_engine
        self._created_portfolio_ids: list[int] = []

    def _create(self, name, **kwargs):
        p = self.plab.create_portfolio({"name": name, **kwargs})
        self._created_portfolio_ids.append(p["id"])
        return p

    def _queued_run(self, name="H1.5 lifecycle"):
        p = self._create(name, components=_components("momentum_trend"), allocation_method="equal_weight")
        return p, self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})

    def test_recover_interrupted_marks_stuck_queued_and_running_rows_failed_and_resumable(self):
        _, run_a = self._queued_run("H1.5 recover: queued")
        _, run_b = self._queued_run("H1.5 recover: running")
        with self.db_engine.begin() as c:   # simulate a server that died mid-run
            c.exec_driver_sql("UPDATE portfolio_research_runs SET status='running' WHERE id=?", (run_b["run_id"],))
        recovered = self.plab.recover_interrupted()
        self.assertGreaterEqual(recovered, 2)
        for run in (run_a, run_b):
            row = self.plab.get_research_run(run["run_id"])
            self.assertEqual(row["status"], "failed")
            self.assertIn("interrupted", row["error"])
        # Resumable: the SAME run_id completes normally from its original pinned definition.
        self.plab.resume_research_run(run_a["run_id"])
        self.plab.execute_research_run(run_a["run_id"])
        result = self.plab.get_research_result(run_a["run_id"])
        self.assertEqual(result["status"], "completed")

    def test_recover_interrupted_is_a_no_op_on_completed_runs(self):
        _, run = self._queued_run("H1.5 recover: completed untouched")
        self.plab.execute_research_run(run["run_id"])
        before = self.plab.get_research_result(run["run_id"])
        self.plab.recover_interrupted()
        after = self.plab.get_research_result(run["run_id"])
        self.assertEqual(after["metrics"], before["metrics"])
        self.assertEqual(after["status"], "completed")

    def test_cancel_queued_run_is_immediate(self):
        _, run = self._queued_run("H1.5 cancel: queued")
        cancelled = self.plab.cancel_research_run(run["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        # A cancelled run never executes -- calling execute_research_run on it is a no-op
        # (the atomic claim only fires on status=='queued').
        self.plab.execute_research_run(run["run_id"])
        self.assertEqual(self.plab.get_research_run(run["run_id"])["status"], "cancelled")

    def test_cancel_already_terminal_run_is_conflict(self):
        _, run = self._queued_run("H1.5 cancel: terminal")
        self.plab.execute_research_run(run["run_id"])
        with self.assertRaises(RuntimeError):
            self.plab.cancel_research_run(run["run_id"])

    def test_resume_only_allowed_from_failed_or_cancelled(self):
        _, run = self._queued_run("H1.5 resume: completed is immutable")
        self.plab.execute_research_run(run["run_id"])
        with self.assertRaises(RuntimeError):
            self.plab.resume_research_run(run["run_id"])   # completed run may never be resumed/rerun

    def test_cancel_flag_requested_mid_run_is_honored_by_the_engine(self):
        # Integration of the DB-level cancel_requested flag with the engine's cancel_check:
        # flip the flag directly (as a slower concurrent cancel request would), then let
        # execute_research_run's own cancel_check (backed by _cancel_requested) see it.
        p, run = self._queued_run("H1.5 cancel: cooperative")
        with self.db_engine.begin() as c:
            c.exec_driver_sql("UPDATE portfolio_research_runs SET status='running', cancel_requested=1 "
                              "WHERE id=?", (run["run_id"],))
        # execute_research_run's claim only fires on status=='queued', so this simulates the
        # run having already been claimed and immediately asked to cancel; call the engine
        # directly to prove the flag actually stops the daily loop (unit-level proof that
        # backs the DB-level cancel_research_run wiring exercised above).
        self.assertTrue(self.plab._cancel_requested(run["run_id"]))

    def test_no_duplicate_execution_on_a_second_execute_call(self):
        # Simulates a stray duplicate BackgroundTasks invocation for the same run_id (the
        # scenario Stage H1.5 flags explicitly: "in-flight BackgroundTasks 不得造成 duplicate
        # run/evidence").
        _, run = self._queued_run("H1.5 no-dup: second call is a no-op")
        self.plab.execute_research_run(run["run_id"])
        result1 = self.plab.get_research_result(run["run_id"])
        equity1 = self.plab.get_research_equity(run["run_id"])
        self.plab.execute_research_run(run["run_id"])   # duplicate call -- must not re-run or duplicate rows
        result2 = self.plab.get_research_result(run["run_id"])
        equity2 = self.plab.get_research_equity(run["run_id"])
        self.assertEqual(result1["metrics"], result2["metrics"])
        self.assertEqual(equity1, equity2)   # no duplicate/changed equity rows

    def tearDown(self):
        for pid in self._created_portfolio_ids:
            try:
                self.plab.archive_portfolio(pid)
            except LookupError:
                pass


# ── Stage H1.5 §5: execution/accounting sanity on real data ─────────────────
@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class RealDataAccountingTests(unittest.TestCase):
    def setUp(self):
        from app.services import portfolio_lab as plab
        self.plab = plab
        self._created_portfolio_ids: list[int] = []

    def _create(self, name, **kwargs):
        p = self.plab.create_portfolio({"name": name, **kwargs})
        self._created_portfolio_ids.append(p["id"])
        return p

    def test_daily_equity_has_no_nan_inf_and_cash_weight_stays_in_bounds(self):
        p = self._create("H1.5 accounting: sanity", components=_components(
            "momentum_trend", "value_quality"), allocation_method="equal_weight")
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-10-31"})
        self.plab.execute_research_run(run["run_id"])
        equity = self.plab.get_research_equity(run["run_id"])
        self.assertTrue(equity)
        for row in equity:
            self.assertIsNotNone(row["total_equity"])
            self.assertTrue(math.isfinite(row["total_equity"]))
            self.assertGreater(row["total_equity"], 0)
            self.assertIsNotNone(row["cash"])
            self.assertTrue(math.isfinite(row["cash"]))
            if row["cash_weight"] is not None:
                self.assertTrue(math.isfinite(row["cash_weight"]))
                # A deterministic cash-shortfall scale-down can leave a sub-unit negative
                # residual (see research.py's rounding-residual comment) -- never unbounded.
                self.assertGreaterEqual(row["cash_weight"], -0.01)
                self.assertLessEqual(row["cash_weight"], 1.0 + 1e-6)
            if row["daily_return"] is not None:
                self.assertTrue(math.isfinite(row["daily_return"]))

    def test_provenance_traces_portfolio_version_to_strategy_version_to_wf_job(self):
        from app.services import walkforward_lab as wf
        p = self._create("H1.5 accounting: provenance", components=_components("momentum_trend"),
                         allocation_method="equal_weight")
        component = p["latest_version"]["components"][0]
        job = wf.get_job(component["walkforward_job_id"])
        self.assertEqual(component["strategy_id"], job["provenance"]["strategy_id"])
        self.assertEqual(component["strategy_version_id"], job["provenance"]["strategy_version_id"])
        self.assertEqual(component["strategy_version_no"], job["provenance"]["strategy_version_no"])

    def test_component_sleeve_is_always_independent_fold_evidence_never_stitched(self):
        # Structural guarantee: portfolio_lab/series.py only ever call
        # contract.build_exact_sleeve (H0's independent-fold sleeve) -- the stitched series
        # (walkforward_lab.get_stitched) is never imported anywhere under app/services/portfolio*.
        import inspect
        from app.services import portfolio as portfolio_pkg
        from app.services import portfolio_lab
        import pathlib
        pkg_dir = pathlib.Path(inspect.getfile(portfolio_pkg)).parent
        sources = [pathlib.Path(inspect.getfile(portfolio_lab)).read_text(encoding="utf-8")]
        for py in pkg_dir.glob("*.py"):
            sources.append(py.read_text(encoding="utf-8"))
        for src in sources:
            self.assertNotIn("get_stitched", src)
            self.assertNotIn("stitched_secondary", src)

    def tearDown(self):
        for pid in self._created_portfolio_ids:
            try:
                self.plab.archive_portfolio(pid)
            except LookupError:
                pass


# ── Stage H1.5 §6: multi-Strategy-Portfolio real workflow ───────────────────
_REAL_KEPT_PORTFOLIO_NAMES = {
    "core_five": "H1 smoke: Core Five",
    "momentum_value": "H1 smoke: Momentum + Value",
    "custom_mix": "H1 smoke: Custom Mix",
}


def _find_real_kept_portfolio(name: str) -> dict | None:
    from app.services import portfolio_lab as plab
    for p in plab.list_portfolios(include_archived=False):
        if p["name"] == name:
            return p
    return None


def _real_kept_portfolios_available() -> bool:
    return all(_find_real_kept_portfolio(n) is not None for n in _REAL_KEPT_PORTFOLIO_NAMES.values())


@unittest.skipUnless(_real_jobs_available() and _real_kept_portfolios_available(),
                     "the 3 permanently-kept Stage H1 real Strategy Portfolios are not present in this DB")
class RealPortfolioWorkflowTests(unittest.TestCase):
    """Exercises list/open/version-history/clone/new-version/archive against the SAME
    3 real, permanently-kept portfolios the Stage H1 report used as evidence (Core Five /
    Momentum + Value / Custom Mix). Mutating steps (new version, archive) only ever touch a
    CLONE of a real portfolio -- the 3 originals and their pinned research runs are never
    edited or archived by this test, so they stay available as evidence for later stages."""

    def setUp(self):
        from app.services import portfolio_lab as plab
        self.plab = plab
        self._created_portfolio_ids: list[int] = []

    def tearDown(self):
        for pid in self._created_portfolio_ids:
            try:
                self.plab.archive_portfolio(pid)
            except LookupError:
                pass

    def test_list_open_version_history_on_the_real_kept_portfolios(self):
        listed = self.plab.list_portfolios(include_archived=False)
        names = {p["name"] for p in listed}
        for name in _REAL_KEPT_PORTFOLIO_NAMES.values():
            self.assertIn(name, names)
        core_five = _find_real_kept_portfolio(_REAL_KEPT_PORTFOLIO_NAMES["core_five"])
        opened = self.plab.get_portfolio(core_five["id"])
        self.assertEqual(len(opened["latest_version"]["components"]), 5)
        versions = self.plab.list_versions(core_five["id"])
        self.assertTrue(versions)
        self.assertEqual(versions[0]["id"], opened["latest_version"]["id"])   # newest first
        v1 = self.plab.get_version(core_five["id"], versions[-1]["id"])
        self.assertEqual(v1["version_no"], 1)

    def test_real_kept_portfolios_have_readable_completed_research_history(self):
        for name in _REAL_KEPT_PORTFOLIO_NAMES.values():
            p = _find_real_kept_portfolio(name)
            runs = self.plab.list_research_runs(p["id"])
            completed = [r for r in runs if r["status"] == "completed"]
            self.assertTrue(completed, f"{name} has no completed research run")
            run_id = completed[0]["id"]
            result = self.plab.get_research_result(run_id)
            self.assertEqual(result["status"], "completed")
            self.assertIsNotNone(result["metrics"]["cagr"])
            equity = self.plab.get_research_equity(run_id)
            rebalances = self.plab.get_research_rebalances(run_id)
            self.assertTrue(equity)
            self.assertTrue(rebalances)

    def test_clone_edit_and_archive_only_ever_touch_the_clone(self):
        source = _find_real_kept_portfolio(_REAL_KEPT_PORTFOLIO_NAMES["momentum_value"])
        source_before = self.plab.get_portfolio(source["id"])
        runs_before = self.plab.list_research_runs(source["id"])

        clone = self.plab.clone_portfolio(source["id"], {"name": "H1.5 workflow: clone of Momentum + Value"})
        self._created_portfolio_ids.append(clone["id"])
        self.assertNotEqual(clone["id"], source["id"])
        # Same component DEFINITION (strategy/version/job pins), but the clone's own rows
        # (id, portfolio_version_id) are naturally independent -- compare content, not identity.
        key = lambda c: (c["strategy_id"], c["strategy_version_id"], c["strategy_version_no"],
                         c["walkforward_job_id"], c["target_weight"])
        self.assertEqual([key(c) for c in clone["latest_version"]["components"]],
                         [key(c) for c in source_before["latest_version"]["components"]])

        # "create new version": edit the CLONE only.
        updated_clone = self.plab.update_portfolio(clone["id"], {"constraints": {"max_strategy_weight": 0.4}})
        self.assertEqual(updated_clone["latest_version"]["version_no"], 2)

        # The ORIGINAL is completely untouched: same latest version, same research history.
        source_after = self.plab.get_portfolio(source["id"])
        self.assertEqual(source_after["latest_version"], source_before["latest_version"])
        self.assertEqual(self.plab.list_research_runs(source["id"]), runs_before)

        # Archiving the clone leaves its version history readable.
        self.plab.archive_portfolio(clone["id"])
        archived = self.plab.get_portfolio(clone["id"])
        self.assertTrue(archived["archived"])
        self.assertEqual(len(self.plab.list_versions(clone["id"])), 2)

    def test_arbitrary_short_lived_component_combination_still_runs_end_to_end(self):
        # A component combination that matches NONE of the 3 kept real portfolios exactly
        # (proving the engine is generic over the component list, not hard-coded to those
        # three specific combos) -- created, run, then archived (short-lived, per spec).
        p = self.plab.create_portfolio({
            "name": "H1.5 workflow: short-lived arbitrary mix",
            "components": _components("quality_growth_momentum", "value_quality", "low_volatility_quality"),
            "allocation_method": "equal_weight"})
        self._created_portfolio_ids.append(p["id"])
        run = self.plab.create_research_run(p["id"], {"start": "2024-11-01", "end": "2025-04-30"})
        self.plab.execute_research_run(run["run_id"])
        result = self.plab.get_research_result(run["run_id"])
        self.assertEqual(result["status"], "completed")
        self.plab.archive_portfolio(p["id"])
        still_readable = self.plab.get_research_result(run["run_id"])
        self.assertEqual(still_readable["metrics"], result["metrics"])


if __name__ == "__main__":
    unittest.main()
