"""Stage H0 Tasks 3 and 6: cross-strategy netting contract.

Pure functions only — no allocation engine. A strategy-level capital allocation
(``{strategy_key: fraction_of_portfolio}``) is always an input supplied by the caller
(Stage H1+); this module only defines how sleeve-level exposures and orders COMBINE
once that allocation exists, so five sleeves sharing one pool of capital never (a)
silently double-count a stock five different strategies happen to hold, or (b) trade
against each other on the same name within the same rebalance.

Uses each sleeve's EXACT daily stock weight (``contract.build_exact_sleeve`` /
``ledger.replay_sleeve``), never Stage G's entry-notional approximation — that
approximation was explicitly "descriptive only, not for capital allocation" (Stage G
report section 7 / Portfolio Lab blocker 3).
"""
from __future__ import annotations

from typing import Mapping

NETTING_CONTRACT_VERSION = "portfolio-netting/h0/1"


def aggregate_stock_exposure(strategy_allocation: Mapping[str, float],
                             sleeve_stock_weight: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    """``aggregate_weight[stock] = sum_strategy allocation[s] * sleeve_stock_weight[s][stock]``.
    A strategy present in ``strategy_allocation`` but missing from ``sleeve_stock_weight``
    (e.g. its sleeve failed to load) simply contributes nothing here — see
    ``baseline.apply_baseline`` for what that missing allocation should do instead
    (stay cash), which this function does not decide."""
    out: dict[str, float] = {}
    for strat, alloc in strategy_allocation.items():
        if not alloc:
            continue
        for sid, weight in sleeve_stock_weight.get(strat, {}).items():
            if weight:
                out[sid] = out.get(sid, 0.0) + alloc * weight
    return out


def strategy_contribution_by_stock(strategy_allocation: Mapping[str, float],
                                   sleeve_stock_weight: Mapping[str, Mapping[str, float]]
                                   ) -> dict[str, dict[str, float]]:
    """``{stock: {strategy: contributed_weight}}`` — the same numbers as
    ``aggregate_stock_exposure``, broken out by contributor rather than summed, so a
    caller can explain why a stock's aggregate exposure is large."""
    out: dict[str, dict[str, float]] = {}
    for strat, alloc in strategy_allocation.items():
        if not alloc:
            continue
        for sid, weight in sleeve_stock_weight.get(strat, {}).items():
            if weight:
                out.setdefault(sid, {})[strat] = alloc * weight
    return out


def total_cash(strategy_allocation: Mapping[str, float],
               sleeve_cash_weight: Mapping[str, float]) -> float:
    """A strategy missing from ``sleeve_cash_weight`` defaults to 100% cash for its
    slice — consistent with ``baseline``'s no-candidate policy, and safer than
    treating a missing sleeve as fully invested."""
    return sum(alloc * sleeve_cash_weight.get(strat, 1.0) for strat, alloc in strategy_allocation.items())


def max_single_stock_exposure_hook(aggregate_weight: Mapping[str, float],
                                   limit: float | None = None) -> dict:
    """Reporting hook only — computes the max aggregate single-stock exposure and
    whether it breaches an optional ``limit``. Does not enforce or rebalance anything;
    no allocation engine exists yet in H0."""
    if not aggregate_weight:
        return {"max_stock": None, "max_weight": 0.0, "limit": limit, "breached": False}
    sid, weight = max(aggregate_weight.items(), key=lambda kv: kv[1])
    return {"max_stock": sid, "max_weight": weight, "limit": limit,
            "breached": limit is not None and weight > limit}


def sector_exposure_hook(aggregate_weight: Mapping[str, float],
                         sector_by_stock: Mapping[str, str | None],
                         limit: float | None = None) -> dict:
    """Reporting hook only. Uses the CURRENT (non point-in-time) ``stocks.industry``
    snapshot, same limitation already disclosed in Stage G section 4 — never claims a
    historical sector for a historically-held position."""
    by_sector: dict[str, float] = {}
    for sid, weight in aggregate_weight.items():
        sector = sector_by_stock.get(sid) or "unknown"
        by_sector[sector] = by_sector.get(sector, 0.0) + weight
    breached = sorted(s for s, w in by_sector.items() if limit is not None and w > limit)
    return {"exposure_by_sector": by_sector, "limit": limit, "breached_sectors": breached,
            "sector_source": "stocks.industry (current snapshot, not point-in-time)"}


def net_orders(sleeve_orders: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    """Task 6 execution contract, step 2 (aggregate/net): ``sleeve_orders[strategy][stock]``
    is that sleeve's OWN desired signed share delta for this rebalance, computed
    independently of the other sleeves (step 1: sleeves produce desired exposures).
    Returns the single netted order per stock — e.g. five sleeves that would otherwise
    each buy and sell the same name at once collapse into whatever the net actually is,
    including exactly zero. Orders that net to (numerically) zero are dropped."""
    out: dict[str, float] = {}
    for orders in sleeve_orders.values():
        for sid, delta in orders.items():
            out[sid] = out.get(sid, 0.0) + delta
    return {sid: delta for sid, delta in out.items() if abs(delta) > 1e-9}


def naive_summed_cost(sleeve_orders: Mapping[str, Mapping[str, float]],
                      price_by_stock: Mapping[str, float], cost_config) -> float:
    """What five sleeves EACH trading their own order independently would cost in total
    — the baseline ``net_transaction_cost`` is defined to avoid paying."""
    from app.services.strategy import costs as _costs
    total = 0.0
    for orders in sleeve_orders.values():
        for sid, delta in orders.items():
            price = price_by_stock.get(sid)
            if price is None or delta == 0:
                continue
            side = "buy" if delta > 0 else "sell"
            total += _costs.calculate(side, abs(delta), price, cost_config).total
    return total


def net_transaction_cost(sleeve_orders: Mapping[str, Mapping[str, float]],
                         price_by_stock: Mapping[str, float], cost_config) -> float:
    """Task 6: portfolio transaction cost must be based on the NET order, not a simple
    sum of what each sleeve's own (pre-netting) order would have cost alone."""
    from app.services.strategy import costs as _costs
    net = net_orders(sleeve_orders)
    total = 0.0
    for sid, delta in net.items():
        price = price_by_stock.get(sid)
        if price is None:
            continue
        side = "buy" if delta > 0 else "sell"
        total += _costs.calculate(side, abs(delta), price, cost_config).total
    return total
