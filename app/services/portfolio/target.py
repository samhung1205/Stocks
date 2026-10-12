"""Stage H1 Task 5: resolve ONE rebalance date's aggregate portfolio target.

Pure function: strategy portfolio version (method + config + constraints) +
each pinned component's point-in-time sleeve state on this date -> aggregate
target stock weights + cash weight + per-stock strategy contribution +
diagnostics. Ties together, in the fixed order the stage instructions specify:

    allocation method -> no-candidate cash baseline -> aggregate exposure
    -> enforce/report constraints

Never reads a date later than ``as_of`` — every date-bearing input the caller
passes in (``prior_returns_by_component``) must already be leakage-filtered
before this module sees it (``series.prior_returns`` already only includes
``date < as_of``); this module does not re-check dates itself, it only combines
already-point-in-time inputs.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from . import allocation, baseline, constraints as constraints_mod, netting

TARGET_CONTRACT_VERSION = "portfolio-target/h1/1"


def resolve_rebalance_target(*, as_of: str, component_keys: Sequence[str], method: str,
                             candidate_status: Mapping[str, str],
                             sleeve_stock_weight: Mapping[str, Mapping[str, float]],
                             portfolio_constraints: Mapping,
                             target_weight: Mapping[str, float] | None = None,
                             prior_returns_by_component: Mapping[str, Sequence[float]] | None = None) -> dict:
    alloc = allocation.resolve(method, component_keys, target_weight=target_weight,
                               prior_returns_by_component=prior_returns_by_component)
    if alloc["status"] != "ready":
        return {"contract_version": TARGET_CONTRACT_VERSION, "as_of": as_of, "status": alloc["status"],
                "reason": alloc.get("reason"), "allocation": alloc, "stock_weight": {}, "cash_weight": 1.0,
                "contribution_by_stock": {}, "constraint_diagnostics": []}

    baseline_result = baseline.apply_baseline(alloc["weights"], candidate_status)
    strategic_cash = alloc.get("strategic_cash", 0.0)  # fixed_weight's un-allocated remainder

    strat_clamp = constraints_mod.apply_max_strategy_weight(
        baseline_result["in_market_allocation"], portfolio_constraints.get("max_strategy_weight"))
    in_market = strat_clamp["weights"]

    raw_stock_weight = netting.aggregate_stock_exposure(in_market, sleeve_stock_weight)
    contribution = netting.strategy_contribution_by_stock(in_market, sleeve_stock_weight)

    stock_clamp = constraints_mod.apply_max_single_stock_weight(
        raw_stock_weight, portfolio_constraints.get("max_single_stock_weight"))
    min_cash_result = constraints_mod.apply_min_cash(stock_clamp["weights"], portfolio_constraints.get("min_cash"))
    stock_weight = {sid: w for sid, w in min_cash_result["weights"].items() if w > 1e-12}

    cash_weight = 1.0 - sum(stock_weight.values())
    diagnostics = strat_clamp["diagnostics"] + stock_clamp["diagnostics"] + min_cash_result["diagnostics"]

    return {
        "contract_version": TARGET_CONTRACT_VERSION, "as_of": as_of, "status": "ready",
        "allocation": alloc,
        "no_candidate_cash_allocation": baseline_result["cash_allocation"],
        "strategic_cash": strategic_cash,
        "stock_weight": stock_weight,
        "contribution_by_stock": contribution,
        "cash_weight": cash_weight,
        "constraint_diagnostics": diagnostics,
    }
