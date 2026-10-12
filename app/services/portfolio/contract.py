"""Stage H0 Task 2: Portfolio sleeve contract v2 — the exact, mark-to-market sleeve.

Versioned separately from Stage G's ``cross_strategy.sleeve`` (``cross-strategy-sleeve/g/1``,
entry-notional weights, descriptive-only). That v1 contract is untouched — this is a new,
additional shape, not a silent reinterpretation of the old one. v2 replaces "weight at
entry" with a real daily mark-to-market series built by ``ledger.replay_sleeve`` and
reconciled against the stored independent OOS equity (``reconciliation.py``).
"""
from __future__ import annotations

from typing import Mapping

from .data import FoldData, StrategyData, closes_for, corporate_actions_for, initial_cash_for_job
from .ledger import EXACT_LEDGER_CONTRACT_VERSION, replay_sleeve
from .reconciliation import reconcile

PORTFOLIO_SLEEVE_CONTRACT_VERSION = "portfolio-sleeve/h0/2"

REQUIRED_FIELDS = (
    "contract_version", "strategy_id", "strategy_version_id", "strategy_version_no",
    "fold_index", "evidence_type", "candidate_status", "daily", "turnover", "cost_drag_pct",
    "warnings", "reconciliation",
)
DAILY_ROW_FIELDS = (
    "date", "cash", "shares_by_stock", "mtm_value_by_stock", "stock_weight_by_stock",
    "cash_weight", "total_equity", "daily_return",
)


def _no_candidate_daily(dates_and_equity: list[tuple[str, float]]) -> list[dict]:
    return [{
        "date": d, "cash": equity, "shares_by_stock": {}, "mtm_value_by_stock": {},
        "stock_weight_by_stock": {}, "cash_weight": 1.0, "total_equity": equity,
        "daily_return": 0.0, "missing_marks": [],
    } for d, equity in dates_and_equity]


def build_exact_sleeve(sd: StrategyData, fold_index: int, job_id: str) -> dict:
    """Build the v2 contract for one strategy's one fold. Raises ``LookupError`` if the
    fold was not loaded (same contract as Stage G's ``sleeve.build``)."""
    fold = sd.folds.get(fold_index)
    if fold is None:
        raise LookupError(f"{sd.key}: fold {fold_index} not loaded")

    if fold.outcome == "no_candidate":
        dates_and_equity = sorted((p["date"], p["net_equity"]) for p in fold.equity)
        daily = _no_candidate_daily(dates_and_equity)
    else:
        dates = sorted(p["date"] for p in fold.equity)
        stock_ids = {f["stock_id"] for f in fold.fills}
        test_start, test_end = fold.windows["test"]
        actions = corporate_actions_for(stock_ids, test_start, test_end)
        closes = closes_for(stock_ids, test_start, test_end)
        initial_cash = initial_cash_for_job(job_id)
        daily = replay_sleeve(dates, fold.fills, actions, closes, initial_cash)

    recon = reconcile(fold.equity, daily)
    sev = fold.severity or {}
    return {
        "contract_version": PORTFOLIO_SLEEVE_CONTRACT_VERSION,
        "ledger_contract_version": EXACT_LEDGER_CONTRACT_VERSION,
        "strategy_id": sd.template_key,
        "strategy_version_id": sd.provenance.get("strategy_version_id"),
        "strategy_version_no": sd.provenance.get("strategy_version_no"),
        "fold_index": fold_index,
        "evidence_type": "independent_fold",
        "candidate_status": fold.outcome,
        "daily": daily,
        "turnover": (fold.oos_metrics or {}).get("turnover"),
        "cost_drag_pct": (fold.oos_metrics or {}).get("cost_drag_pct"),
        "warnings": {"max_severity": sev.get("max_severity"), "counts": sev.get("counts")},
        "reconciliation": recon,
    }


def validate(sleeve: Mapping) -> list[str]:
    """Structural checks only (shape + internal consistency, not a re-derivation of the
    values). Empty list = contract satisfied. Does NOT check ``reconciliation.reconciled``
    — a caller must check that separately and treat False as a correctness issue, not a
    validation failure to swallow here."""
    problems = []
    for f in REQUIRED_FIELDS:
        if f not in sleeve:
            problems.append(f"missing field: {f}")
    if problems:
        return problems
    if sleeve["evidence_type"] != "independent_fold":
        problems.append("evidence_type must be independent_fold (stitched is secondary evidence)")
    if sleeve["candidate_status"] not in ("evaluated", "no_candidate"):
        problems.append(f"unexpected candidate_status: {sleeve['candidate_status']}")
    dates = [r["date"] for r in sleeve["daily"]]
    if dates != sorted(dates):
        problems.append("daily rows must be sorted by date")
    for row in sleeve["daily"]:
        for f in DAILY_ROW_FIELDS:
            if f not in row:
                problems.append(f"daily row {row.get('date')}: missing field {f}")
        if problems:
            continue
        if row["total_equity"] is not None and row["cash_weight"] is not None:
            stock_w = sum(w for w in row["stock_weight_by_stock"].values() if w is not None)
            total_w = stock_w + row["cash_weight"]
            if abs(total_w - 1.0) > 1e-6:
                problems.append(f"{row['date']}: stock_weight+cash_weight={total_w} != 1")
    if sleeve["candidate_status"] == "no_candidate":
        if any(r["shares_by_stock"] for r in sleeve["daily"]):
            problems.append("no_candidate fold must have empty holdings")
        if any(r["cash_weight"] != 1.0 for r in sleeve["daily"]):
            problems.append("no_candidate fold must be 100% cash every date")
        if any(r["daily_return"] != 0.0 for r in sleeve["daily"]):
            problems.append("no_candidate fold daily_return must be 0")
    return problems


def build_all(strategies: Mapping[str, StrategyData], job_ids: Mapping[str, str]) -> dict:
    sleeves = []
    problems: dict[str, list[str]] = {}
    unreconciled: dict[str, dict] = {}
    for key, sd in strategies.items():
        job_id = job_ids[key]
        for idx in sorted(sd.folds):
            sleeve = build_exact_sleeve(sd, idx, job_id)
            found = validate(sleeve)
            if found:
                problems[f"{key}/fold{idx}"] = found
            if not sleeve["reconciliation"]["reconciled"]:
                unreconciled[f"{key}/fold{idx}"] = sleeve["reconciliation"]
            sleeves.append(sleeve)
    return {
        "contract_version": PORTFOLIO_SLEEVE_CONTRACT_VERSION, "required_fields": list(REQUIRED_FIELDS),
        "sleeves": sleeves, "validation_problems": problems, "all_valid": not problems,
        "unreconciled": unreconciled, "all_reconciled": not unreconciled,
    }
