"""Stage G Task 7: the Portfolio-Lab readiness data contract ("sleeve").

A sleeve is one strategy's one Walk Forward fold, reshaped into the fields a future Portfolio
Lab would need to combine sleeves into a portfolio. This module only BUILDS and VALIDATES that
shape from data this stage already has (via ``data.py`` / ``overlap.py``, both independent-fold
evidence) — it does not compute any capital allocation, and no stitched (secondary) evidence is
ever put in a sleeve.
"""
from __future__ import annotations

from typing import Mapping

from .data import StrategyData
from .drawdown import daily_returns
from .overlap import holdings_by_date

SLEEVE_CONTRACT_VERSION = "cross-strategy-sleeve/g/1"

REQUIRED_FIELDS = (
    "contract_version", "strategy_id", "strategy_version_id", "strategy_version_no",
    "fold_index", "evidence_type", "candidate_status", "dates", "return_by_date",
    "equity_by_date", "cash_weight_by_date", "target_holdings_by_rebalance_date",
    "turnover", "cost_drag_pct", "warnings",
)


def build(sd: StrategyData, fold_index: int) -> dict:
    fold = sd.folds.get(fold_index)
    if fold is None:
        raise LookupError(f"{sd.key}: fold {fold_index} not loaded")
    dates = sorted(p["date"] for p in fold.equity)
    equity_by_date = {p["date"]: p["net_equity"] for p in fold.equity}
    returns = daily_returns(fold.equity)
    if fold.outcome == "no_candidate":
        cash_weight_by_date = {d: 1.0 for d in dates}
        rebalances: dict[str, list[dict]] = {}
    else:
        snaps = holdings_by_date(fold.equity, fold.fills)
        rebalances = {d: [{"stock_id": sid, "weight": h["weight"]} for sid, h in holdings.items()]
                     for d, holdings in snaps.items()}
        cash_weight_by_date = {
            d: max(0.0, 1.0 - sum((h["weight"] or 0.0) for h in holdings.values()))
            for d, holdings in snaps.items()}
    sev = fold.severity or {}
    return {
        "contract_version": SLEEVE_CONTRACT_VERSION,
        "strategy_id": sd.template_key,
        "strategy_version_id": sd.provenance.get("strategy_version_id"),
        "strategy_version_no": sd.provenance.get("strategy_version_no"),
        "fold_index": fold_index,
        "evidence_type": "independent_fold",
        "candidate_status": fold.outcome,
        "dates": dates,
        "return_by_date": returns,
        "equity_by_date": equity_by_date,
        "cash_weight_by_date": cash_weight_by_date,
        "cash_weight_note": "only known at rebalance dates; entry-notional weight approximation, "
                            "not a daily mark-to-market cash balance",
        "target_holdings_by_rebalance_date": rebalances,
        "turnover": (fold.oos_metrics or {}).get("turnover"),
        "cost_drag_pct": (fold.oos_metrics or {}).get("cost_drag_pct"),
        "warnings": {"max_severity": sev.get("max_severity"), "counts": sev.get("counts")},
    }


def validate(sleeve: Mapping) -> list[str]:
    """Structural checks only (shape, not values). Empty list = contract satisfied."""
    problems = []
    for field in REQUIRED_FIELDS:
        if field not in sleeve:
            problems.append(f"missing field: {field}")
    if problems:
        return problems
    if sleeve["evidence_type"] != "independent_fold":
        problems.append("evidence_type must be independent_fold (stitched is secondary evidence)")
    if sleeve["candidate_status"] not in ("evaluated", "no_candidate"):
        problems.append(f"unexpected candidate_status: {sleeve['candidate_status']}")
    if sleeve["candidate_status"] == "no_candidate":
        if any(w != 1.0 for w in sleeve["cash_weight_by_date"].values()):
            problems.append("no_candidate fold must be 100% cash every date")
        if sleeve["target_holdings_by_rebalance_date"]:
            problems.append("no_candidate fold must not carry target holdings")
    if not isinstance(sleeve["dates"], list) or sleeve["dates"] != sorted(sleeve["dates"]):
        problems.append("dates must be a sorted list")
    return problems


def build_all(strategies: Mapping[str, StrategyData]) -> dict:
    sleeves = []
    problems = {}
    for key, sd in strategies.items():
        for idx in sorted(sd.folds):
            sleeve = build(sd, idx)
            found = validate(sleeve)
            if found:
                problems[f"{key}/fold{idx}"] = found
            sleeves.append(sleeve)
    return {"contract_version": SLEEVE_CONTRACT_VERSION, "required_fields": list(REQUIRED_FIELDS),
            "sleeves": sleeves, "validation_problems": problems,
            "all_valid": not problems}
