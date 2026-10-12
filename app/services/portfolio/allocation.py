"""Stage H1 Task 3: allocation methods over an arbitrary, generic component roster.

Every function here takes only ``component_keys`` (opaque strings — H1 uses
``str(strategy_id)``, never a template name) plus the evidence a specific method
needs. Nothing here special-cases a strategy identity: a caller with 1, 5 or 50
components calls the exact same code path. ``equal_weight``/``fixed_weight`` are
real, implemented allocations; ``inverse_volatility`` computes real weights but
only once ``portfolio.readiness.inverse_volatility_readiness`` (fed
leakage-filtered prior returns) reports the whole method ``ready`` — otherwise it
refuses (``insufficient_evidence`` / ``unavailable``) rather than silently
falling back to equal weight or any other guess. ``risk_parity`` /
``minimum_variance`` / ``correlation_aware`` are intentionally NOT implemented
here yet (Stage H0's ``readiness.covariance_readiness`` already reports them
``unavailable`` for the current five-strategy evidence; H1 does not force a
method the readiness contract says isn't computable) — ``resolve`` raises
``NotImplementedError`` naming them, so a caller can never silently get zeros.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from . import readiness

ALLOCATION_METHODS = ("equal_weight", "fixed_weight", "inverse_volatility")
NOT_YET_IMPLEMENTED_METHODS = ("risk_parity", "minimum_variance", "correlation_aware")
_NOT_YET_IMPLEMENTED = NOT_YET_IMPLEMENTED_METHODS  # internal alias

ALLOCATION_CONTRACT_VERSION = "portfolio-allocation/h1/1"


def equal_weight(component_keys: Sequence[str]) -> dict:
    """3A: equal weight over the FULL component list of this portfolio version —
    a ``no_candidate`` component's slice is handled afterwards by
    ``portfolio.baseline.apply_baseline``, not by shrinking the denominator here."""
    n = len(component_keys)
    if n == 0:
        return {"method": "equal_weight", "status": "unavailable", "weights": {}, "reason": "no components"}
    w = 1.0 / n
    return {"method": "equal_weight", "status": "ready", "weights": {k: w for k in component_keys}}


def fixed_weight(component_keys: Sequence[str], target_weight: Mapping[str, float]) -> dict:
    """3B: user-specified per-component target weight. ``weights >= 0`` and
    ``sum(weights) <= 1``; the remainder is strategic cash (never auto-invested).
    A component with no explicit target weight defaults to 0 (it is simply not
    allocated capital by this method — it is not an error, e.g. a component added
    for future use). Raises ``ValueError`` on a negative weight or a total over 1
    (both are definition errors, not a runtime allocation state to report softly)."""
    weights = {}
    for k in component_keys:
        w = target_weight.get(k, 0.0) or 0.0
        if w < 0:
            raise ValueError(f"fixed_weight: component {k} has negative target_weight {w}")
        weights[k] = float(w)
    total = sum(weights.values())
    if total > 1.0 + 1e-9:
        raise ValueError(f"fixed_weight: sum of target weights {total} exceeds 1.0")
    return {"method": "fixed_weight", "status": "ready", "weights": weights,
            "strategic_cash": max(0.0, 1.0 - total)}


def inverse_volatility(component_keys: Sequence[str],
                       prior_returns_by_component: Mapping[str, Sequence[float]]) -> dict:
    """3C: weight[k] proportional to 1/volatility, computed ONLY over
    ``prior_returns_by_component`` the caller has already point-in-time filtered
    (``portfolio.leakage.prior_only_returns``). If ANY component's evidence is
    insufficient, or there are no components, the WHOLE method is not ready —
    matches Stage H0's readiness contract (``all strategies ready -> ready``) so a
    caller can never get a partial, silently-reweighted result."""
    r = readiness.inverse_volatility_readiness(
        {k: prior_returns_by_component.get(k, ()) for k in component_keys})
    if r["status"] != "ready":
        return {"method": "inverse_volatility", "status": r["status"], "weights": {}, "readiness": r}
    inv = {k: (1.0 / v["vol"] if v["vol"] else 0.0) for k, v in r["per_strategy"].items()}
    total_inv = sum(inv.values())
    if total_inv <= 0:
        return {"method": "inverse_volatility", "status": "unavailable", "weights": {},
                "reason": "all volatilities are zero", "readiness": r}
    weights = {k: v / total_inv for k, v in inv.items()}
    return {"method": "inverse_volatility", "status": "ready", "weights": weights, "readiness": r}


def resolve(method: str, component_keys: Sequence[str], *, target_weight: Mapping[str, float] | None = None,
           prior_returns_by_component: Mapping[str, Sequence[float]] | None = None) -> dict:
    """Single dispatch point Stage H1's target-construction engine calls — the ONLY
    place a rebalance date's allocation method name is switched on. Adding a new
    Single Strategy never touches this function; only adding a genuinely NEW
    allocation method does."""
    if method == "equal_weight":
        return equal_weight(component_keys)
    if method == "fixed_weight":
        return fixed_weight(component_keys, target_weight or {})
    if method == "inverse_volatility":
        return inverse_volatility(component_keys, prior_returns_by_component or {})
    if method in _NOT_YET_IMPLEMENTED:
        raise NotImplementedError(
            f"allocation method {method!r} is capability/readiness-only in Stage H1 "
            f"(see portfolio.readiness.covariance_readiness) and has no runnable "
            f"implementation yet")
    raise ValueError(f"unknown allocation method: {method!r}")
