"""Stage H0 Task 4: the first, default no-candidate baseline policy.

Decision: a strategy whose sleeve is ``no_candidate`` at date T keeps its ENTIRE
allocation slice in cash. It is never automatically handed to the other, still-active
strategies. Redistribution is left as a named, future, OPT-IN policy — not implemented,
not the default, so nothing can silently start redistributing capital just because a
caller passes a flag that used to be a no-op.
"""
from __future__ import annotations

from typing import Mapping

NO_CANDIDATE_BASELINE_VERSION = "portfolio-no-candidate-baseline/h0/1"


def apply_baseline(strategy_allocation: Mapping[str, float], candidate_status: Mapping[str, str],
                   *, redistribute: bool = False) -> dict:
    """``candidate_status``: ``{strategy_key: "evaluated" | "no_candidate"}`` for the
    allocation date in question; a strategy missing from this mapping is treated as
    ``no_candidate`` (fail safe to cash, never to invested)."""
    if redistribute:
        raise NotImplementedError(
            "redistribution-on-no-candidate is a future, optional policy and is not "
            "implemented in Stage H0; the only implemented baseline is: a no_candidate "
            "strategy's allocation stays in cash")
    in_market: dict[str, float] = {}
    cash: dict[str, float] = {}
    for strat, alloc in strategy_allocation.items():
        status = candidate_status.get(strat, "no_candidate")
        if status == "evaluated":
            in_market[strat], cash[strat] = alloc, 0.0
        elif status == "no_candidate":
            in_market[strat], cash[strat] = 0.0, alloc
        else:
            raise ValueError(f"{strat}: unexpected candidate_status {status!r}")
    return {
        "contract_version": NO_CANDIDATE_BASELINE_VERSION,
        "policy": "no_candidate_stays_cash",
        "in_market_allocation": in_market,
        "cash_allocation": cash,
        "total_cash_fraction": sum(cash.values()),
    }
