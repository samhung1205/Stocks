"""Stage H1 Task 4: versioned portfolio constraints.

Every clamp here is a deterministic, disclosed rule that only ever moves excess
weight INTO cash — never a hidden renormalization that redistributes a breach
onto other components/stocks (that would silently change what the allocation
method decided). Each function returns the adjusted weights AND a diagnostics
list naming exactly what was clamped and by how much, so a caller/report can
always explain a portfolio's actual exposure vs. what the raw allocation wanted.

``max_sector_weight`` is intentionally NOT enforced here (Stage H0's
``netting.sector_exposure_hook`` limitation applies unchanged: current, not
point-in-time, ``stocks.industry``) — Stage H1 only implements REPORTING for it,
same as H0 left it. Enforcing a constraint on data known to be non-point-in-time
would silently launder that limitation into a "real" constraint.
"""
from __future__ import annotations

from typing import Mapping

CONSTRAINTS_CONTRACT_VERSION = "portfolio-constraints/h1/1"


def apply_max_strategy_weight(weights: Mapping[str, float], limit: float | None) -> dict:
    """Clamp each component's (post no-candidate-baseline, in-market) weight to
    ``limit``; the clipped amount becomes additional cash for THAT component only
    — never handed to another component."""
    if limit is None:
        return {"weights": dict(weights), "cash_added": 0.0, "diagnostics": []}
    adjusted, diagnostics, cash_added = {}, [], 0.0
    for k, w in weights.items():
        if w > limit:
            diagnostics.append({"component": k, "raw_weight": w, "limit": limit, "clamped_to": limit,
                                "excess_to_cash": w - limit})
            cash_added += w - limit
            adjusted[k] = limit
        else:
            adjusted[k] = w
    return {"weights": adjusted, "cash_added": cash_added, "diagnostics": diagnostics}


def apply_max_single_stock_weight(stock_weights: Mapping[str, float], limit: float | None) -> dict:
    """Clamp each stock's AGGREGATE (already-netted, cross-strategy) weight to
    ``limit``; excess becomes cash. Never redistributed to other stocks — a
    breach on 2330 does not silently increase exposure to 2454."""
    if limit is None:
        return {"weights": dict(stock_weights), "cash_added": 0.0, "diagnostics": []}
    adjusted, diagnostics, cash_added = {}, [], 0.0
    for sid, w in stock_weights.items():
        if w > limit:
            diagnostics.append({"stock_id": sid, "raw_weight": w, "limit": limit, "clamped_to": limit,
                                "excess_to_cash": w - limit})
            cash_added += w - limit
            adjusted[sid] = limit
        else:
            adjusted[sid] = w
    return {"weights": adjusted, "cash_added": cash_added, "diagnostics": diagnostics}


def apply_min_cash(stock_weights: Mapping[str, float], min_cash: float | None) -> dict:
    """If the implied cash weight (``1 - sum(stock_weights)``) is below
    ``min_cash``, scale EVERY stock weight down by the same proportional factor
    until cash reaches exactly ``min_cash``. This is a single, disclosed,
    whole-portfolio haircut rule — distinct from (and applied after) the
    no-redistribution constraints above, which only ever move a SPECIFIC
    component/stock's excess to cash without touching anyone else's weight."""
    if not min_cash:
        return {"weights": dict(stock_weights), "diagnostics": []}
    total = sum(stock_weights.values())
    implied_cash = 1.0 - total
    if implied_cash >= min_cash - 1e-12 or total <= 0:
        return {"weights": dict(stock_weights), "diagnostics": []}
    scale = (1.0 - min_cash) / total
    adjusted = {sid: w * scale for sid, w in stock_weights.items()}
    return {"weights": adjusted, "diagnostics": [{
        "rule": "min_cash_proportional_haircut", "min_cash": min_cash,
        "implied_cash_before": implied_cash, "scale_factor": scale}]}


DEFAULT_CONSTRAINTS = {
    "max_strategy_weight": None, "max_single_stock_weight": None,
    "max_sector_weight": None, "min_cash": 0.0,
}


def validate_constraints(constraints: Mapping) -> dict:
    """Structural validation + fill defaults; unknown keys are rejected so a typo
    in a versioned config is never silently ignored."""
    unknown = set(constraints) - set(DEFAULT_CONSTRAINTS)
    if unknown:
        raise ValueError(f"unknown constraint key(s): {sorted(unknown)}")
    out = dict(DEFAULT_CONSTRAINTS)
    out.update(constraints)
    for key in ("max_strategy_weight", "max_single_stock_weight", "max_sector_weight"):
        v = out[key]
        if v is not None and not (0 < v <= 1):
            raise ValueError(f"{key} must be in (0, 1] or null")
    if not (0 <= out["min_cash"] < 1):
        raise ValueError("min_cash must be in [0, 1)")
    return out
