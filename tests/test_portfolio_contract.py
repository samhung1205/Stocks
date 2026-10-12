"""Stage H0 tests: exact sleeve ledger, portfolio sleeve contract v2, cross-strategy
netting, no-candidate baseline, allocation-method readiness, leakage guards.

``PortfolioRealDBReconciliationTests`` runs against the real local dev DB (read-only)
using the same completed Walk Forward jobs Stage F1.5 / Stage G already validated
(``momentum_trend``, ``value_quality``, ``quality_growth_momentum`` for a no_candidate
sleeve) — it is skipped if those jobs are not present in this environment's DB.
"""
from __future__ import annotations

import copy
import unittest

from app.services.strategy.types import CostConfig

from app.services.portfolio import baseline, contract, leakage, ledger, netting, readiness
from app.services.portfolio.data import load_strategy
from app.services.portfolio.reconciliation import reconcile


# ── ledger.replay_sleeve ────────────────────────────────────────────────────
class LedgerReplayTests(unittest.TestCase):
    def test_buy_then_sell_cash_math_matches_stored_fill_fields(self):
        dates = ["2025-01-02", "2025-01-03"]
        fills = [
            {"date": "2025-01-02", "stock_id": "1101", "side": "buy", "shares": 100.0,
             "fill_price": 50.0, "commission": 20.0, "tax": 0.0},
            {"date": "2025-01-03", "stock_id": "1101", "side": "sell", "shares": 100.0,
             "fill_price": 55.0, "commission": 20.0, "tax": 15.0},
        ]
        closes = {"1101": {"2025-01-02": 51.0, "2025-01-03": 55.0}}
        rows = ledger.replay_sleeve(dates, fills, [], closes, initial_cash=1_000_000.0)
        expected_cash_day0 = 1_000_000.0 - (100 * 50.0 + 20.0)
        self.assertAlmostEqual(rows[0]["cash"], expected_cash_day0)
        self.assertAlmostEqual(rows[0]["shares_by_stock"]["1101"], 100.0)
        self.assertAlmostEqual(rows[0]["mtm_value_by_stock"]["1101"], 100 * 51.0)
        expected_cash_day1 = expected_cash_day0 + (100 * 55.0 - 20.0 - 15.0)
        self.assertAlmostEqual(rows[1]["cash"], expected_cash_day1)
        self.assertNotIn("1101", rows[1]["shares_by_stock"])
        self.assertAlmostEqual(rows[1]["total_equity"], expected_cash_day1)

    def test_corporate_action_share_multiplier_and_cash_dividend(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        fills = [{"date": "2025-01-02", "stock_id": "1101", "side": "buy", "shares": 100.0,
                  "fill_price": 50.0, "commission": 0.0, "tax": 0.0}]
        actions = [{"date": "2025-01-03", "stock_id": "1101", "cash_dividend": 2.0, "share_multiplier": None},
                   {"date": "2025-01-06", "stock_id": "1101", "cash_dividend": None, "share_multiplier": 1.05}]
        closes = {"1101": {"2025-01-02": 50.0, "2025-01-03": 48.0, "2025-01-06": 47.5}}
        rows = ledger.replay_sleeve(dates, fills, actions, closes, initial_cash=1_000_000.0)
        cash_after_buy = 1_000_000.0 - 100 * 50.0
        self.assertAlmostEqual(rows[1]["cash"], cash_after_buy + 100 * 2.0)
        self.assertAlmostEqual(rows[1]["shares_by_stock"]["1101"], 100.0)
        self.assertAlmostEqual(rows[2]["shares_by_stock"]["1101"], 105.0)
        self.assertAlmostEqual(rows[2]["mtm_value_by_stock"]["1101"], 105.0 * 47.5)

    def test_action_on_stock_not_held_is_a_no_op(self):
        dates = ["2025-01-02"]
        actions = [{"date": "2025-01-02", "stock_id": "9999", "cash_dividend": 5.0, "share_multiplier": None}]
        rows = ledger.replay_sleeve(dates, [], actions, {}, initial_cash=1_000_000.0)
        self.assertEqual(rows[0]["cash"], 1_000_000.0)
        self.assertEqual(rows[0]["shares_by_stock"], {})

    def test_unresolved_action_both_fields_none_is_a_no_op(self):
        dates = ["2025-01-02", "2025-01-03"]
        fills = [{"date": "2025-01-02", "stock_id": "1101", "side": "buy", "shares": 100.0,
                  "fill_price": 50.0, "commission": 0.0, "tax": 0.0}]
        actions = [{"date": "2025-01-03", "stock_id": "1101", "cash_dividend": None, "share_multiplier": None}]
        closes = {"1101": {"2025-01-02": 50.0, "2025-01-03": 50.0}}
        rows = ledger.replay_sleeve(dates, fills, actions, closes, initial_cash=1_000_000.0)
        self.assertAlmostEqual(rows[1]["shares_by_stock"]["1101"], 100.0)
        self.assertAlmostEqual(rows[1]["cash"], 1_000_000.0 - 100 * 50.0)

    def test_missing_close_marks_position_as_missing_not_fabricated_zero_weight(self):
        dates = ["2025-01-02"]
        fills = [{"date": "2025-01-02", "stock_id": "1101", "side": "buy", "shares": 100.0,
                  "fill_price": 50.0, "commission": 0.0, "tax": 0.0}]
        rows = ledger.replay_sleeve(dates, fills, [], {}, initial_cash=1_000_000.0)
        self.assertEqual(rows[0]["missing_marks"], ["1101"])
        self.assertIsNone(rows[0]["mtm_value_by_stock"]["1101"])
        self.assertIsNone(rows[0]["stock_weight_by_stock"]["1101"])
        # equity still matches engine._equity()'s prices.get(sid, 0.0) fallback: 0 contribution.
        self.assertAlmostEqual(rows[0]["total_equity"], 1_000_000.0 - 100 * 50.0)

    def test_deterministic(self):
        dates = ["2025-01-02", "2025-01-03"]
        fills = [{"date": "2025-01-02", "stock_id": "1101", "side": "buy", "shares": 100.0,
                  "fill_price": 50.0, "commission": 20.0, "tax": 0.0}]
        closes = {"1101": {"2025-01-02": 51.0, "2025-01-03": 52.0}}
        r1 = ledger.replay_sleeve(dates, fills, [], closes, 1_000_000.0)
        r2 = ledger.replay_sleeve(dates, fills, [], closes, 1_000_000.0)
        self.assertEqual(r1, r2)


# ── contract.validate ───────────────────────────────────────────────────────
class ContractValidateTests(unittest.TestCase):
    def _evaluated_sleeve(self):
        return {
            "contract_version": contract.PORTFOLIO_SLEEVE_CONTRACT_VERSION,
            "ledger_contract_version": ledger.EXACT_LEDGER_CONTRACT_VERSION,
            "strategy_id": "momentum_trend", "strategy_version_id": 1, "strategy_version_no": 1,
            "fold_index": 0, "evidence_type": "independent_fold", "candidate_status": "evaluated",
            "daily": [{"date": "2025-01-02", "cash": 500_000.0, "shares_by_stock": {"1101": 100.0},
                       "mtm_value_by_stock": {"1101": 500_000.0},
                       "stock_weight_by_stock": {"1101": 0.5}, "cash_weight": 0.5,
                       "total_equity": 1_000_000.0, "daily_return": 0.0}],
            "turnover": 1.0, "cost_drag_pct": 0.01, "warnings": {},
            "reconciliation": {"reconciled": True, "mismatches": []},
        }

    def test_stock_weight_plus_cash_weight_sums_to_one(self):
        self.assertEqual(contract.validate(self._evaluated_sleeve()), [])

    def test_weight_sum_violation_is_caught(self):
        bad = self._evaluated_sleeve()
        bad["daily"][0]["cash_weight"] = 0.9  # now 0.5 + 0.9 != 1
        problems = contract.validate(bad)
        self.assertTrue(any("!= 1" in p for p in problems))

    def test_no_candidate_must_be_all_cash_empty_holdings_zero_return(self):
        sleeve = self._evaluated_sleeve()
        sleeve["candidate_status"] = "no_candidate"
        sleeve["daily"] = [{"date": "2025-01-02", "cash": 1_000_000.0, "shares_by_stock": {},
                            "mtm_value_by_stock": {}, "stock_weight_by_stock": {}, "cash_weight": 1.0,
                            "total_equity": 1_000_000.0, "daily_return": 0.0}]
        self.assertEqual(contract.validate(sleeve), [])

    def test_no_candidate_with_holdings_is_rejected(self):
        sleeve = self._evaluated_sleeve()
        sleeve["candidate_status"] = "no_candidate"
        problems = contract.validate(sleeve)
        self.assertTrue(any("empty holdings" in p for p in problems))

    def test_missing_required_field(self):
        sleeve = self._evaluated_sleeve()
        del sleeve["turnover"]
        self.assertIn("missing field: turnover", contract.validate(sleeve))


# ── reconciliation ──────────────────────────────────────────────────────────
class ReconciliationTests(unittest.TestCase):
    def test_matching_series_reconciles(self):
        stored = [{"date": "2025-01-02", "net_equity": 1_000_000.0}]
        daily = [{"date": "2025-01-02", "total_equity": 1_000_000.0}]
        report = reconcile(stored, daily)
        self.assertTrue(report["reconciled"])
        self.assertEqual(report["mismatches"], [])

    def test_value_mismatch_is_flagged_not_normalized(self):
        stored = [{"date": "2025-01-02", "net_equity": 1_000_000.0}]
        daily = [{"date": "2025-01-02", "total_equity": 999_000.0}]
        report = reconcile(stored, daily)
        self.assertFalse(report["reconciled"])
        self.assertEqual(report["mismatches"][0]["reason"], "value_mismatch")
        self.assertAlmostEqual(report["mismatches"][0]["diff"], -1000.0)

    def test_missing_date_on_one_side_is_flagged(self):
        stored = [{"date": "2025-01-02", "net_equity": 1_000_000.0}]
        daily = []
        report = reconcile(stored, daily)
        self.assertFalse(report["reconciled"])
        self.assertEqual(report["mismatches"][0]["reason"], "missing_on_one_side")


# ── netting ──────────────────────────────────────────────────────────────────
class NettingTests(unittest.TestCase):
    def test_overlapping_stock_aggregation(self):
        allocation = {"momentum_trend": 0.6, "value_quality": 0.4}
        weights = {
            "momentum_trend": {"2330": 0.10, "2454": 0.05},
            "value_quality": {"2330": 0.08, "1101": 0.02},
        }
        agg = netting.aggregate_stock_exposure(allocation, weights)
        self.assertAlmostEqual(agg["2330"], 0.6 * 0.10 + 0.4 * 0.08)
        self.assertAlmostEqual(agg["2454"], 0.6 * 0.05)
        self.assertAlmostEqual(agg["1101"], 0.4 * 0.02)

    def test_strategy_contribution_by_stock_breaks_out_the_same_numbers(self):
        allocation = {"a": 0.5, "b": 0.5}
        weights = {"a": {"2330": 0.2}, "b": {"2330": 0.4}}
        contrib = netting.strategy_contribution_by_stock(allocation, weights)
        self.assertAlmostEqual(contrib["2330"]["a"], 0.1)
        self.assertAlmostEqual(contrib["2330"]["b"], 0.2)
        agg = netting.aggregate_stock_exposure(allocation, weights)
        self.assertAlmostEqual(agg["2330"], sum(contrib["2330"].values()))

    def test_missing_sleeve_does_not_raise_and_defaults_its_cash_to_full(self):
        allocation = {"a": 0.5, "b": 0.5}
        weights = {"a": {"2330": 0.2}}  # "b" never loaded
        agg = netting.aggregate_stock_exposure(allocation, weights)
        self.assertEqual(set(agg), {"2330"})
        cash = netting.total_cash(allocation, {"a": 0.8})  # "b" missing -> treated as 100% cash
        self.assertAlmostEqual(cash, 0.5 * 0.8 + 0.5 * 1.0)

    def test_opposite_sleeve_orders_net_to_zero(self):
        sleeve_orders = {"momentum_trend": {"2330": 1000.0}, "value_quality": {"2330": -1000.0}}
        net = netting.net_orders(sleeve_orders)
        self.assertNotIn("2330", net)

    def test_opposite_sleeve_orders_partial_netting(self):
        sleeve_orders = {"momentum_trend": {"2330": 1000.0}, "value_quality": {"2330": -400.0}}
        net = netting.net_orders(sleeve_orders)
        self.assertAlmostEqual(net["2330"], 600.0)

    def test_net_transaction_cost_is_cheaper_than_naive_sum_when_orders_cancel(self):
        sleeve_orders = {"momentum_trend": {"2330": 1000.0}, "value_quality": {"2330": -1000.0}}
        prices = {"2330": 100.0}
        cfg = CostConfig(minimum_commission=20.0)
        net_cost = netting.net_transaction_cost(sleeve_orders, prices, cfg)
        naive_cost = netting.naive_summed_cost(sleeve_orders, prices, cfg)
        self.assertEqual(net_cost, 0.0)
        self.assertGreater(naive_cost, 0.0)

    def test_net_transaction_cost_never_exceeds_naive_sum(self):
        sleeve_orders = {"a": {"2330": 500.0, "2454": 300.0}, "b": {"2330": -200.0, "1101": 400.0}}
        prices = {"2330": 100.0, "2454": 50.0, "1101": 30.0}
        cfg = CostConfig(minimum_commission=20.0)
        self.assertLessEqual(netting.net_transaction_cost(sleeve_orders, prices, cfg),
                             netting.naive_summed_cost(sleeve_orders, prices, cfg))

    def test_max_single_stock_exposure_hook(self):
        agg = {"2330": 0.25, "2454": 0.10}
        report = netting.max_single_stock_exposure_hook(agg, limit=0.2)
        self.assertEqual(report["max_stock"], "2330")
        self.assertTrue(report["breached"])

    def test_max_single_stock_exposure_hook_empty(self):
        report = netting.max_single_stock_exposure_hook({})
        self.assertIsNone(report["max_stock"])
        self.assertFalse(report["breached"])

    def test_sector_exposure_hook_uses_current_snapshot_and_flags_note(self):
        agg = {"2330": 0.3, "2454": 0.2}
        sectors = {"2330": "半導體", "2454": "半導體"}
        report = netting.sector_exposure_hook(agg, sectors, limit=0.4)
        self.assertAlmostEqual(report["exposure_by_sector"]["半導體"], 0.5)
        self.assertIn("半導體", report["breached_sectors"])
        self.assertIn("current snapshot", report["sector_source"])


# ── no-candidate baseline ────────────────────────────────────────────────────
class BaselineTests(unittest.TestCase):
    def test_no_candidate_stays_cash_not_redistributed(self):
        allocation = {"momentum_trend": 0.5, "quality_growth_momentum": 0.5}
        status = {"momentum_trend": "evaluated", "quality_growth_momentum": "no_candidate"}
        result = baseline.apply_baseline(allocation, status)
        self.assertAlmostEqual(result["in_market_allocation"]["momentum_trend"], 0.5)
        self.assertAlmostEqual(result["cash_allocation"]["quality_growth_momentum"], 0.5)
        self.assertAlmostEqual(result["cash_allocation"]["momentum_trend"], 0.0)
        self.assertAlmostEqual(result["total_cash_fraction"], 0.5)

    def test_redistribution_is_not_implemented(self):
        allocation = {"a": 1.0}
        status = {"a": "no_candidate"}
        with self.assertRaises(NotImplementedError):
            baseline.apply_baseline(allocation, status, redistribute=True)

    def test_missing_status_defaults_to_cash(self):
        result = baseline.apply_baseline({"a": 1.0}, {})
        self.assertAlmostEqual(result["cash_allocation"]["a"], 1.0)


# ── leakage guards ───────────────────────────────────────────────────────────
class LeakageTests(unittest.TestCase):
    def test_future_input_raises(self):
        with self.assertRaises(leakage.LeakageError):
            leakage.assert_point_in_time("2026-01-01", {"sleeve_return": "2026-01-02"})

    def test_past_and_present_inputs_pass(self):
        leakage.assert_point_in_time("2026-01-01", {"a": "2025-12-31", "b": "2026-01-01", "c": None})

    def test_prior_only_returns_excludes_as_of_and_future(self):
        series = {"2025-01-02": 0.01, "2025-01-03": 0.02, "2025-01-06": -0.5}
        filtered = leakage.prior_only_returns(series, as_of="2025-01-06")
        self.assertEqual(set(filtered), {"2025-01-02", "2025-01-03"})

    def test_mutating_source_after_filtering_does_not_leak_into_result(self):
        series = {"2025-01-02": 0.01}
        filtered = leakage.prior_only_returns(series, as_of="2025-01-06")
        series["2025-01-05"] = 999.0  # a "future" fold result arriving later
        self.assertEqual(filtered, {"2025-01-02": 0.01})

    def test_inverse_vol_readiness_differs_with_and_without_future_leakage(self):
        # A big, obviously-distinguishing "future" value that must NOT reach the estimate.
        raw_returns = {f"2025-01-{d:02d}": 0.001 for d in range(2, 22)}  # 20 prior days, low vol
        raw_returns["2025-02-10"] = 50.0  # future fold's huge outlier
        as_of = "2025-01-22"
        leak_free = readiness.inverse_volatility_readiness(
            {"a": list(leakage.prior_only_returns(raw_returns, as_of).values())})
        leaked = readiness.inverse_volatility_readiness({"a": list(raw_returns.values())})
        self.assertNotAlmostEqual(leak_free["per_strategy"]["a"]["vol"], leaked["per_strategy"]["a"]["vol"])
        self.assertLess(leak_free["per_strategy"]["a"]["vol"], leaked["per_strategy"]["a"]["vol"])

    def test_future_oos_mutation_does_not_change_past_allocation_inputs(self):
        source = {"2025-01-02": 0.01, "2025-01-03": 0.02}
        as_of = "2025-01-04"
        before = leakage.prior_only_returns(source, as_of)
        mutated = copy.deepcopy(source)
        mutated["2025-01-10"] = -0.9  # a future fold's result changes/arrives later
        after = leakage.prior_only_returns(mutated, as_of)
        self.assertEqual(before, after)


# ── allocation-method readiness ──────────────────────────────────────────────
class ReadinessTests(unittest.TestCase):
    def test_equal_weight_ready_with_any_sleeve(self):
        self.assertEqual(readiness.equal_weight_readiness(["momentum_trend"])["status"], "ready")
        self.assertEqual(readiness.equal_weight_readiness([])["status"], "unavailable")

    def test_inverse_vol_insufficient_sample(self):
        result = readiness.inverse_volatility_readiness({"a": [0.01, 0.02]})
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["per_strategy"]["a"]["vol"])

    def test_covariance_zero_overlap_is_unavailable_never_fabricated(self):
        overlap = {("low_volatility_quality", "quality_growth_momentum"): 0}
        result = readiness.covariance_readiness(
            "correlation_aware", overlap, ["low_volatility_quality", "quality_growth_momentum"])
        self.assertEqual(result["status"], "unavailable")
        pair = result["pairs"]["low_volatility_quality|quality_growth_momentum"]
        self.assertEqual(pair["overlap_days"], 0)
        self.assertNotIn("correlation", pair)  # never a fabricated number

    def test_covariance_thin_overlap_is_insufficient_evidence(self):
        overlap = {("momentum_trend", "value_quality"): 3}  # below MIN_PAIRWISE_OVERLAP_DAYS=5
        result = readiness.covariance_readiness("risk_parity", overlap, ["momentum_trend", "value_quality"])
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_covariance_ready_when_all_pairs_meet_the_floor(self):
        overlap = {("momentum_trend", "value_quality"): 456}
        result = readiness.covariance_readiness("minimum_variance", overlap, ["momentum_trend", "value_quality"])
        self.assertEqual(result["status"], "ready")

    def test_covariance_one_bad_pair_fails_the_whole_method(self):
        overlap = {("a", "b"): 456, ("a", "c"): 0, ("b", "c"): 456}
        result = readiness.covariance_readiness("correlation_aware", overlap, ["a", "b", "c"])
        self.assertEqual(result["status"], "unavailable")

    def test_invalid_method_name_rejected(self):
        with self.assertRaises(ValueError):
            readiness.covariance_readiness("mean_variance", {}, ["a", "b"])

    def test_assess_all_is_deterministic(self):
        sleeve_keys = ["momentum_trend", "value_quality"]
        prior = {"momentum_trend": [0.01] * 25, "value_quality": [0.02] * 25}
        overlap = {("momentum_trend", "value_quality"): 100}
        r1 = readiness.assess_all(sleeve_keys, prior, overlap)
        r2 = readiness.assess_all(sleeve_keys, prior, overlap)
        self.assertEqual(r1, r2)


# ── real DB smoke reconciliation (read-only) ─────────────────────────────────
_REAL_JOBS = {
    "momentum_trend": "9a3612c981e44cf88daabc655abc43ed",
    "value_quality": "098c6aa9252544cc88c910c68c05c490",
    "quality_growth_momentum": "052d3472688740c89d8e4701bbc358cd",  # folds 0-2 no_candidate, 3 evaluated
}


def _real_jobs_available() -> bool:
    try:
        from app.services import walkforward_lab as wf
        for job_id in _REAL_JOBS.values():
            if wf.get_job(job_id)["status"] != "completed":
                return False
        return True
    except LookupError:
        return False


@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class PortfolioRealDBReconciliationTests(unittest.TestCase):
    """Real DB (read-only), same jobs already validated by Stage F1.5 / Stage G:
    momentum_trend and value_quality (both fully evaluated) and quality_growth_momentum
    (a real no_candidate sleeve, folds 0-2) — the exact sleeve is reconstructed purely
    from fills + corporate_actions + daily_quotes and must reconcile against the
    already-stored independent OOS net_equity."""

    def test_momentum_trend_all_folds_reconcile_and_validate(self):
        sd = load_strategy("momentum_trend", _REAL_JOBS["momentum_trend"])
        for idx in sorted(sd.folds):
            sleeve = contract.build_exact_sleeve(sd, idx, _REAL_JOBS["momentum_trend"])
            self.assertEqual(contract.validate(sleeve), [], msg=f"fold {idx}")
            self.assertTrue(sleeve["reconciliation"]["reconciled"], msg=sleeve["reconciliation"]["mismatches"])
            self.assertGreater(sleeve["reconciliation"]["dates_checked"], 0)

    def test_value_quality_all_folds_reconcile_and_validate(self):
        sd = load_strategy("value_quality", _REAL_JOBS["value_quality"])
        for idx in sorted(sd.folds):
            sleeve = contract.build_exact_sleeve(sd, idx, _REAL_JOBS["value_quality"])
            self.assertEqual(contract.validate(sleeve), [], msg=f"fold {idx}")
            self.assertTrue(sleeve["reconciliation"]["reconciled"], msg=sleeve["reconciliation"]["mismatches"])

    def test_no_candidate_sleeve_is_all_cash_and_reconciles(self):
        sd = load_strategy("quality_growth_momentum", _REAL_JOBS["quality_growth_momentum"])
        no_candidate_folds = [idx for idx, f in sd.folds.items() if f.outcome == "no_candidate"]
        self.assertTrue(no_candidate_folds)
        for idx in no_candidate_folds:
            sleeve = contract.build_exact_sleeve(sd, idx, _REAL_JOBS["quality_growth_momentum"])
            self.assertEqual(sleeve["candidate_status"], "no_candidate")
            self.assertTrue(all(r["cash_weight"] == 1.0 for r in sleeve["daily"]))
            self.assertTrue(all(not r["shares_by_stock"] for r in sleeve["daily"]))
            self.assertTrue(all(r["daily_return"] == 0.0 for r in sleeve["daily"]))
            self.assertEqual(contract.validate(sleeve), [])
            self.assertTrue(sleeve["reconciliation"]["reconciled"])

    def test_build_exact_sleeve_is_deterministic_on_real_data(self):
        sd = load_strategy("momentum_trend", _REAL_JOBS["momentum_trend"])
        s1 = contract.build_exact_sleeve(sd, 0, _REAL_JOBS["momentum_trend"])
        s2 = contract.build_exact_sleeve(sd, 0, _REAL_JOBS["momentum_trend"])
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
