"""Stage I1 tests: Financial Planner x Strategy Portfolio backend integration.

Two kinds of coverage:
  - Pure-logic / legacy-backward-compat tests that never need a real Strategy
    Portfolio (allocation math, _names()/_target_key() helpers, legacy ETF
    profile round-trips). These always run.
  - ``StrategyPortfolioSourceRealDBTests``, which needs a real, completed Walk
    Forward job to build a real Strategy Portfolio + research run against (the
    same fixture jobs Stage H1/H1.5's ``tests/test_portfolio_lab.py`` already
    uses). Skipped via ``skipUnless`` if that fixture data isn't present in
    this DB -- see AGENTS.md: real-DB integration tests must degrade
    gracefully, never hard-fail a fresh checkout.

The one invariant every test in this file ultimately protects: Strategy
Portfolio historical/OOS evidence and Planner future planning assumptions are
two separate things, and nothing in ``app/services/finance/`` may silently
copy a number from one into the other.
"""
from __future__ import annotations

import unittest

from app.services.finance import allocation, portfolio_source, universe
from app.services.finance import service as finance

# Same Stage F1.5 real Walk Forward jobs tests/test_portfolio_lab.py validates against.
_REAL_JOBS = {
    "momentum_trend": "9a3612c981e44cf88daabc655abc43ed",
    "value_quality": "098c6aa9252544cc88c910c68c05c490",
}
_RUN_WINDOW = {"start": "2024-11-01", "end": "2025-04-30"}


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


def _component(key: str) -> list[dict]:
    from app.services import walkforward_lab as wf
    job_id = _REAL_JOBS[key]
    strategy_id = wf.get_job(job_id)["provenance"]["strategy_id"]
    return [{"strategy_id": strategy_id, "walkforward_job_id": job_id}]


# ── pure logic: no DB needed ─────────────────────────────────────────────────
class AllocationNoFallbackTests(unittest.TestCase):
    def test_manual_source_skips_role_default_return_fallback(self):
        roles = {"core_tw": 100.0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        # "my_savings_bond" is not in universe.UNIVERSE -> would default to core_tw's 7.0%
        # if treated like a legacy custom ETF ticker; source_types=manual must force None instead.
        result = allocation.allocate(["my_savings_bond"], roles, initial_capital=0, monthly=0,
                                     source_types={"my_savings_bond": "manual"})
        self.assertIsNone(result["targets"][0]["expected_return"])
        self.assertIsNone(result["expected_return"])
        self.assertTrue(any(w["level"] == "assumption" for w in result["warnings"]))

    def test_manual_source_is_not_classified_as_core_tw_or_tw_equity(self):
        roles = {"core_tw": 100.0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(["my_savings_bond"], roles, initial_capital=0, monthly=0,
                                     source_types={"my_savings_bond": "manual"})
        row = result["targets"][0]
        self.assertEqual(row["role"], "manual")
        self.assertNotEqual(row["role"], "core_tw")
        self.assertEqual(row["asset"], "other")
        self.assertNotEqual(row["region"], "TW")
        self.assertEqual(row["region"], "unspecified")

    def test_etf_target_without_override_still_uses_role_default(self):
        roles = {"core_tw": 100.0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(["0050"], roles, initial_capital=0, monthly=0)
        self.assertEqual(result["targets"][0]["expected_return"], universe.default_return("0050"))
        self.assertFalse(any(w["level"] == "assumption" for w in result["warnings"]))

    def test_strategy_portfolio_role_has_no_default_return_and_no_manual_flag_needed(self):
        # The "portfolio:" prefix alone (via universe.meta()) is enough -- source_types
        # is only needed for `manual`, never for `strategy_portfolio`.
        roles = {"core_tw": 100.0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        key = universe.portfolio_key(999999)
        result = allocation.allocate([key], roles, initial_capital=0, monthly=0)
        self.assertIsNone(result["targets"][0]["expected_return"])
        self.assertEqual(result["targets"][0]["role"], "strategy_portfolio")

    def test_unpriced_target_excluded_from_weighted_return_but_keeps_its_weight(self):
        roles = {"core_tw": 0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(
            ["0050", "manual_x"], roles, initial_capital=1_000_000, monthly=0,
            override={"0050": 50, "manual_x": 50}, source_types={"manual_x": "manual"})
        # Priced portion (0050 only) drives the aggregate -- not diluted toward 0 by manual_x.
        self.assertAlmostEqual(result["expected_return"], universe.default_return("0050"))
        by_id = {t["stock_id"]: t for t in result["targets"]}
        self.assertEqual(by_id["manual_x"]["weight"], 50.0)
        self.assertEqual(by_id["manual_x"]["initial_amount"], 500_000)


# ── Stage I1.1 hotfix: manual reporting classification ───────────────────────
class ManualClassificationHotfixTests(unittest.TestCase):
    """AGENTS.md / docs/RESEARCH_CONTRACTS.md §16: source_type='manual' must never be
    reported as core_tw / TW equity -- that fallback is reserved for legacy
    (source_type IS NULL) custom tickers only."""

    def test_explicit_manual_source_is_not_role_core_tw(self):
        self.assertEqual(universe.meta("anything", "manual")["role"], "manual")
        self.assertNotEqual(universe.meta("anything", "manual")["role"], "core_tw")

    def test_manual_source_not_reported_as_tw_equity(self):
        m = universe.meta("anything", "manual")
        self.assertEqual(universe.ROLE_DEFAULTS[m["role"]]["asset"], "other")
        self.assertNotEqual(m["region"], "TW")

    def test_manual_expected_return_only_from_explicit_assumption(self):
        # No returns override -> None, never a role/ETF-style number.
        roles = {"core_tw": 0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(["m1"], roles, initial_capital=0, monthly=0,
                                     source_types={"m1": "manual"})
        self.assertIsNone(result["targets"][0]["expected_return"])
        # An explicit planning assumption (returns override) is still honored.
        result2 = allocation.allocate(["m1"], roles, initial_capital=0, monthly=0,
                                      returns={"m1": 4.2}, source_types={"m1": "manual"})
        self.assertEqual(result2["targets"][0]["expected_return"], 4.2)

    def test_manual_missing_expected_return_no_silent_default_return_fallback(self):
        roles = {"core_tw": 0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(["m1"], roles, initial_capital=0, monthly=0,
                                     source_types={"m1": "manual"})
        self.assertIsNone(result["targets"][0]["expected_return"])
        self.assertIsNone(universe.default_return("m1", "manual"))

    def test_legacy_null_source_custom_symbol_behavior_unchanged(self):
        # source_type=None (the historical, pre-Stage-I1.1 call shape) must still get the
        # original custom-ticker fallback: core_tw / TW / a numeric default return.
        m = universe.meta("some_custom_ticker")
        self.assertEqual(m["role"], "core_tw")
        self.assertEqual(m["region"], "TW")
        self.assertEqual(universe.default_return("some_custom_ticker"), 7.0)
        roles = {"core_tw": 100.0, "core_us": 0, "growth": 0, "income": 0, "defense": 0, "cash": 0}
        result = allocation.allocate(["some_custom_ticker"], roles, initial_capital=0, monthly=0)
        self.assertEqual(result["targets"][0]["role"], "core_tw")
        self.assertEqual(result["targets"][0]["expected_return"], 7.0)

    def test_existing_etf_behavior_unchanged(self):
        m = universe.meta("0050")
        self.assertEqual(m["role"], "core_tw")
        self.assertEqual(m["region"], "TW")
        self.assertEqual(universe.default_return("0050"), 7.0)
        self.assertEqual(universe.default_yield("0050"), 3.0)

    def test_existing_strategy_portfolio_classification_unchanged(self):
        key = universe.portfolio_key(1)
        m = universe.meta(key)
        self.assertEqual(m["role"], "strategy_portfolio")
        self.assertEqual(m["region"], "TW")
        self.assertIsNone(universe.default_return(key))
        # Passing an unrelated source_type must not confuse the prefix-based detection.
        self.assertEqual(universe.meta(key, "etf")["role"], "strategy_portfolio")


class NamesHelperTests(unittest.TestCase):
    def test_names_does_not_treat_portfolio_key_as_a_stock(self):
        # Must not raise (would, if it tried to bind "portfolio:1" as a stock_id param)
        # and must not resolve a name for it.
        names = finance._names(["portfolio:1", "CASH"])
        self.assertNotIn("portfolio:1", names)
        self.assertNotIn("CASH", names)


class TargetKeyHelperTests(unittest.TestCase):
    def test_target_key_derives_opaque_key_for_unsaved_draft(self):
        t = {"source_type": "strategy_portfolio", "strategy_portfolio_id": 42}
        self.assertEqual(finance._target_key(t), "portfolio:42")

    def test_target_key_falls_back_to_stock_id_for_etf_and_manual(self):
        self.assertEqual(finance._target_key({"stock_id": "0050"}), "0050")
        self.assertIsNone(finance._target_key({}))


# ── legacy ETF profiles: backward compatibility ──────────────────────────────
class LegacyBackwardCompatTests(unittest.TestCase):
    def setUp(self):
        self._profile_ids: list[int] = []

    def tearDown(self):
        for pid in self._profile_ids:
            finance.delete_profile(pid)

    def _payload(self):
        return {
            "name": "I1 test: legacy etf",
            "age": 30, "retire_age": 65, "monthly_invest": 20000,
            "assumptions": {"custom_weights": True, "use_portfolio_return": False,
                            "return_conservative": 4, "return_base": 7, "return_optimistic": 10},
            "targets": [
                {"stock_id": "0050", "weight": 60, "enabled": 1},
                {"stock_id": "00719B", "weight": 40, "enabled": 1},
            ],
        }

    def test_legacy_etf_profile_simulation_numerics_unchanged(self):
        result = finance.simulate(self._payload())
        expected = round((60 * universe.default_return("0050") + 40 * universe.default_return("00719B")) / 100, 2)
        self.assertEqual(result["allocation"]["expected_return"], expected)
        self.assertFalse(any(w["level"] in ("assumption",) for w in result["allocation"]["warnings"]))

    def test_nullable_migration_backward_compatible_round_trip(self):
        # No source_type/pin/assumption_source keys at all in the payload -- exactly what
        # every pre-Stage-I1 saved profile looks like. Must round-trip with the new columns
        # simply absent (NULL), and simulate() output must match the legacy-shape computation.
        saved = finance.save_profile(self._payload())
        self._profile_ids.append(saved["id"])
        for t in saved["targets"]:
            self.assertIsNone(t["source_type"])
            self.assertIsNone(t["strategy_portfolio_id"])
            self.assertIsNone(t["portfolio_version_id"])
            self.assertIsNone(t["research_run_id"])
            self.assertIsNone(t["assumption_source"])
            self.assertIsNone(t["assumption_evidence_ref"])
        reloaded = finance.get_profile(saved["id"])
        result = finance.simulate(reloaded)
        expected = round((60 * universe.default_return("0050") + 40 * universe.default_return("00719B")) / 100, 2)
        self.assertEqual(result["allocation"]["expected_return"], expected)

    def test_simulate_is_deterministic_for_a_plain_etf_profile(self):
        payload = self._payload()
        self.assertEqual(finance.simulate(payload), finance.simulate(payload))

    def test_manual_source_missing_expected_return_has_warning_no_silent_fallback(self):
        payload = self._payload()
        payload["targets"].append({"stock_id": "my_custom_note", "source_type": "manual",
                                   "weight": 0, "enabled": 1})
        payload["assumptions"]["custom_weights"] = True
        payload["targets"][0]["weight"] = payload["targets"][1]["weight"] = 40
        payload["targets"][2]["weight"] = 20
        result = finance.simulate(payload)
        warnings = result["allocation"]["warnings"]
        self.assertTrue(any("planning return not configured" in w["message"] for w in warnings))
        by_id = {t["stock_id"]: t for t in result["allocation"]["targets"]}
        self.assertIsNone(by_id["my_custom_note"]["expected_return"])
        self.assertEqual(by_id["my_custom_note"]["weight"], 20.0)   # money/weight preserved
        # Stage I1.1: reporting classification must not leak the legacy custom-ticker
        # fallback (core_tw / TW / 3% dividend yield) into a source_type='manual' target.
        self.assertEqual(by_id["my_custom_note"]["role"], "manual")
        self.assertNotEqual(by_id["my_custom_note"]["region"], "TW")
        # Only "0050" (weight 40) is genuinely TW -- if "my_custom_note" (weight 20) had
        # leaked into the TW bucket via the old core_tw fallback, this would read 60, not 40.
        self.assertEqual(result["allocation"]["exposure"]["region"]["TW"], 40.0)


# ── Strategy Portfolio as an investment source: needs real fixtures ─────────
@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class StrategyPortfolioSourceRealDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.services import portfolio_lab as plab
        cls.plab = plab
        cls._portfolio_ids: list[int] = []

        p1 = plab.create_portfolio({"name": "I1 test: source A", "allocation_method": "equal_weight",
                                    "components": _component("momentum_trend")})
        cls._portfolio_ids.append(p1["id"])
        run1 = plab.create_research_run(p1["id"], _RUN_WINDOW)
        plab.execute_research_run(run1["run_id"])
        cls.p1_id = p1["id"]
        cls.v1_id = p1["latest_version"]["id"]
        cls.run1_id = run1["run_id"]

        p2 = plab.create_portfolio({"name": "I1 test: source B", "allocation_method": "equal_weight",
                                    "components": _component("value_quality")})
        cls._portfolio_ids.append(p2["id"])
        run2 = plab.create_research_run(p2["id"], _RUN_WINDOW)
        plab.execute_research_run(run2["run_id"])
        cls.p2_id = p2["id"]
        cls.v2_id = p2["latest_version"]["id"]
        cls.run2_id = run2["run_id"]

        cls.ev1 = plab.get_research_result(cls.run1_id)
        cls.cagr1 = cls.ev1["metrics"]["cagr"]
        assert cls.cagr1 is not None, "fixture run should produce a real CAGR over a 6-month window"

    @classmethod
    def tearDownClass(cls):
        for pid in cls._portfolio_ids:
            try:
                cls.plab.archive_portfolio(pid)
            except LookupError:
                pass

    def setUp(self):
        self._profile_ids: list[int] = []

    def tearDown(self):
        for pid in self._profile_ids:
            finance.delete_profile(pid)

    def _save(self, target_overrides: dict) -> dict:
        target = {"source_type": "strategy_portfolio", "strategy_portfolio_id": self.p1_id,
                  "portfolio_version_id": self.v1_id, "weight": 100, "enabled": 1}
        target.update(target_overrides)
        saved = finance.save_profile({"name": "I1 test: profile", "targets": [target]})
        self._profile_ids.append(saved["id"])
        return saved

    # -- pin validation --------------------------------------------------
    def test_valid_pin_derives_opaque_stock_id(self):
        saved = self._save({})
        t = saved["targets"][0]
        self.assertEqual(t["stock_id"], universe.portfolio_key(self.p1_id))
        self.assertEqual(t["strategy_portfolio_id"], self.p1_id)
        self.assertEqual(t["portfolio_version_id"], self.v1_id)

    def test_mismatched_version_rejected(self):
        with self.assertRaises(ValueError):
            self._save({"portfolio_version_id": self.v1_id + 999999})

    def test_mismatched_research_run_rejected(self):
        # run2 belongs to portfolio p2/v2, not p1/v1.
        with self.assertRaises(ValueError):
            self._save({"research_run_id": self.run2_id})

    def test_stock_id_prefix_is_reserved_for_strategy_portfolio_source(self):
        with self.assertRaises(ValueError):
            finance.save_profile({"name": "I1 test: bad prefix",
                                  "targets": [{"stock_id": universe.portfolio_key(self.p1_id),
                                              "weight": 100, "enabled": 1}]})

    def test_pin_fields_rejected_outside_strategy_portfolio_source(self):
        with self.assertRaises(ValueError):
            finance.save_profile({"name": "I1 test: stray pin",
                                  "targets": [{"stock_id": "0050", "weight": 100, "enabled": 1,
                                              "strategy_portfolio_id": self.p1_id}]})

    # -- evidence read API -------------------------------------------------
    def test_research_run_id_null_evidence_response(self):
        ev = portfolio_source.evidence(self.p1_id, self.v1_id, None)
        self.assertFalse(ev["research"]["available"])
        self.assertEqual(ev["research"]["reason"], "no_research_run_selected")
        self.assertIn("components", ev)   # identity/components still shown

    def test_archived_portfolio_evidence_still_readable(self):
        p = self.plab.create_portfolio({"name": "I1 test: archive-then-read",
                                        "allocation_method": "equal_weight",
                                        "components": _component("momentum_trend")})
        run = self.plab.create_research_run(p["id"], _RUN_WINDOW)
        self.plab.execute_research_run(run["run_id"])
        self.plab.archive_portfolio(p["id"])
        ev = portfolio_source.evidence(p["id"], p["latest_version"]["id"], run["run_id"])
        self.assertTrue(ev["archived"])
        self.assertTrue(ev["research"]["available"])

    def test_new_portfolio_version_does_not_mutate_old_planner_pin(self):
        saved = self._save({"research_run_id": self.run1_id})
        self.plab.update_portfolio(self.p1_id, {"constraints": {"max_strategy_weight": 0.9}})
        reloaded = finance.get_profile(saved["id"])
        t = reloaded["targets"][0]
        self.assertEqual(t["portfolio_version_id"], self.v1_id)   # unchanged, never auto-relinked
        ev = portfolio_source.evidence(self.p1_id, self.v1_id, self.run1_id)
        self.assertTrue(ev["research"]["available"])
        self.assertEqual(ev["version_no"], 1)

    def test_future_research_run_does_not_mutate_old_projection_inputs(self):
        saved = self._save({"research_run_id": self.run1_id, "expected_return": 3.0,
                            "assumption_source": "research_informed"})
        run_again = self.plab.create_research_run(self.p1_id, {"start": "2024-11-01", "end": "2025-05-31"})
        self.plab.execute_research_run(run_again["run_id"])
        reloaded = finance.get_profile(saved["id"])
        t = reloaded["targets"][0]
        self.assertEqual(t["research_run_id"], self.run1_id)
        self.assertEqual(t["expected_return"], 3.0)
        self.assertEqual(t["assumption_evidence_ref"]["research_run_id"], self.run1_id)
        self.assertEqual(t["assumption_evidence_ref"]["snapshot_cagr"], self.cagr1)

    # -- evidence vs. assumption separation (the core invariant) -----------
    def test_expected_return_never_auto_filled_from_cagr(self):
        saved = self._save({"research_run_id": self.run1_id})   # no expected_return given
        t = saved["targets"][0]
        self.assertIsNone(t["expected_return"])
        self.assertNotEqual(self.cagr1, None)   # evidence genuinely has a number...
        self.assertIsNone(t["expected_return"])  # ...but it was never copied in.

    def test_research_informed_requires_explicit_expected_return(self):
        with self.assertRaises(ValueError):
            self._save({"research_run_id": self.run1_id, "assumption_source": "research_informed"})

    def test_research_informed_requires_evidence(self):
        with self.assertRaises(ValueError):
            self._save({"expected_return": 5.0, "assumption_source": "research_informed"})  # no research_run_id

    def test_research_informed_valid_case_and_evidence_ref_is_audit_snapshot(self):
        chosen_return = round(self.cagr1 / 2, 2)   # deliberately NOT the same number as evidence
        saved = self._save({"research_run_id": self.run1_id, "expected_return": chosen_return,
                            "assumption_source": "research_informed"})
        t = saved["targets"][0]
        self.assertEqual(t["assumption_source"], "research_informed")
        self.assertEqual(t["expected_return"], chosen_return)
        ref = t["assumption_evidence_ref"]
        self.assertEqual(ref["portfolio_version_id"], self.v1_id)
        self.assertEqual(ref["research_run_id"], self.run1_id)
        self.assertEqual(ref["snapshot_cagr"], self.cagr1)
        self.assertIn("snapshot_asof", ref)
        # The audit snapshot and the live assumption are allowed to disagree -- proving
        # the snapshot is a record, not a binding that drives expected_return.
        self.assertNotEqual(t["expected_return"], ref["snapshot_cagr"])

    def test_missing_expected_return_has_warning_and_no_silent_fallback_in_simulate(self):
        saved = self._save({"research_run_id": self.run1_id})
        profile = finance.get_profile(saved["id"])
        profile["age"], profile["retire_age"] = 30, 65
        # strategy_portfolio has no role_weights() anchor (see allocation.role_weights),
        # so its rule-based suggested weight is 0% unless the user's own weight is used.
        profile["assumptions"] = {"custom_weights": True}
        result = finance.simulate(profile)
        warnings = result["allocation"]["warnings"]
        key = universe.portfolio_key(self.p1_id)
        self.assertTrue(any("planning return not configured" in w["message"] and key in w["message"]
                           for w in warnings))
        by_id = {t["stock_id"]: t for t in result["allocation"]["targets"]}
        self.assertIsNone(by_id[key]["expected_return"])
        self.assertEqual(by_id[key]["weight"], 100.0)   # allocation/money unaffected

    # -- cash semantics (Model A) -------------------------------------------
    def test_no_actual_value_fallback_from_research_equity(self):
        saved = self._save({})   # no actual_value given
        profile = finance.get_profile(saved["id"])
        profile["age"], profile["retire_age"] = 30, 65
        result = finance.simulate(profile)
        equity_rows = self.plab.get_research_equity(self.run1_id)
        final_equity = equity_rows[-1]["total_equity"]
        self.assertGreater(final_equity, 0)   # the run genuinely has a large real equity number...
        # ...but rebalance.analyze() sees total actual value 0 (never substituted from it):
        # a single 100%-weight target with no actual_value has no priced holdings at all.
        self.assertFalse(result["rebalance"]["has_holdings"])
        self.assertEqual(result["rebalance"]["total_value"], 0)

    def test_no_journal_positions_fallback_for_strategy_portfolio(self):
        from unittest.mock import patch
        key = universe.portfolio_key(self.p1_id)
        with patch("app.services.journal.positions",
                  return_value=[{"stock_id": key, "market_value": 999_999}]):
            actuals = finance._portfolio_actuals([key])
        self.assertEqual(actuals, {})   # journal fallback never applies to a portfolio key

    def test_no_internal_portfolio_cash_double_counting(self):
        saved = self._save({"actual_value": 1_000_000})
        profile = finance.get_profile(saved["id"])
        profile.update({"age": 30, "retire_age": 65,
                        "asset_cash": 0, "asset_deposit": 0, "asset_stock": 0,
                        "asset_etf": 0, "asset_bond": 0, "asset_other": 0})
        result = finance.simulate(profile)
        # Model A: the portfolio's manual actual_value never leaks into the personal
        # cash/invested-assets health check -- those come solely from Step2 fields.
        self.assertEqual(result["health"]["invested_assets"], 0)
        self.assertEqual(result["health"]["liquid_cash"], 0)
        key = universe.portfolio_key(self.p1_id)
        self.assertTrue(result["rebalance"]["has_holdings"])
        rb_row = next(r for r in result["rebalance"]["rows"] if r["stock_id"] == key)
        self.assertEqual(rb_row["value"], 1_000_000)   # counted exactly once, in rebalance only
        self.assertEqual(result["rebalance"]["total_value"], 1_000_000)   # not doubled

    def test_simulate_deterministic_with_strategy_portfolio_source(self):
        saved = self._save({"research_run_id": self.run1_id, "expected_return": 5.0,
                            "assumption_source": "research_informed"})
        profile = finance.get_profile(saved["id"])
        profile["age"], profile["retire_age"] = 30, 65
        self.assertEqual(finance.simulate(profile), finance.simulate(profile))


# ── API route wiring: /api/finance/portfolio-evidence + /api/finance/profiles error mapping ──
@unittest.skipUnless(_real_jobs_available(), "Stage F1.5 real Walk Forward jobs not present in this DB")
class EvidenceRouteTests(unittest.TestCase):
    """No lifespan -- reuses whatever app.db.engine already points at (the real, already-
    migrated dev DB); does not start the scheduler or call recover_interrupted()."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services import portfolio_lab as plab
        cls.client = TestClient(app)
        cls.plab = plab
        p = plab.create_portfolio({"name": "I1 test: route evidence", "allocation_method": "equal_weight",
                                   "components": _component("momentum_trend")})
        cls.p_id = p["id"]
        cls.v_id = p["latest_version"]["id"]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.plab.archive_portfolio(cls.p_id)
        except LookupError:
            pass

    def test_evidence_route_no_research_run_returns_200_not_500(self):
        r = self.client.get("/api/finance/portfolio-evidence", params={
            "strategy_portfolio_id": self.p_id, "portfolio_version_id": self.v_id})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["research"]["available"])
        self.assertEqual(body["research"]["reason"], "no_research_run_selected")

    def test_evidence_route_unknown_portfolio_is_404_not_500(self):
        r = self.client.get("/api/finance/portfolio-evidence", params={
            "strategy_portfolio_id": 999999999, "portfolio_version_id": 1})
        self.assertEqual(r.status_code, 404)

    def test_save_profile_route_maps_pin_mismatch_to_400_not_500(self):
        r = self.client.post("/api/finance/profiles", json={
            "name": "I1 test: route save",
            "targets": [{"source_type": "strategy_portfolio", "strategy_portfolio_id": self.p_id,
                        "portfolio_version_id": self.v_id + 999999, "weight": 100, "enabled": 1}]})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
