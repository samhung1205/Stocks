"""Stage G Task 1: OOS behavior comparison across strategies. Pure — no DB, no ranking.

Reuses each strategy's own ``walkforward.diagnostics.analyze()`` summary (the same
descriptive object F1/F2 already compute and store on the job) and lays five of them
side by side. Nothing here scores, ranks, or recommends a strategy.
"""
from __future__ import annotations

from typing import Mapping

from .data import StrategyData

OOS_METRIC_KEYS = ("sharpe", "cagr", "max_drawdown", "turnover", "cost_drag_pct", "net_return")


def _fold_table(sd: StrategyData) -> list[dict]:
    rows = []
    for idx in sorted(sd.folds):
        fold = sd.folds[idx]
        row = {"fold": idx, "outcome": fold.outcome, "windows": fold.windows}
        if fold.outcome == "evaluated":
            row["oos"] = {k: (fold.oos_metrics or {}).get(k) for k in OOS_METRIC_KEYS}
            row["unresolved_action_count"] = (fold.oos or {}).get("unresolved_action_count")
            row["partial_rebalance_count"] = (fold.oos or {}).get("partial_rebalance_count")
            row["first_oos_fill"] = (fold.oos or {}).get("effective_first_fill_date")
        sev = fold.severity or {}
        row["max_severity"] = sev.get("max_severity")
        row["training_max_severity"] = (sev.get("training") or {}).get("max_severity")
        rows.append(row)
    return rows


def compare(strategies: Mapping[str, StrategyData]) -> dict:
    """One block per strategy: fold outcomes/metrics table + its own diagnostics summary
    (degradation, parameter stability, cost/turnover dispersion, severity, completeness).
    Descriptive only — see each strategy's ``fold_dispersion`` note (n <= 4 folds)."""
    per_strategy = {}
    for key, sd in strategies.items():
        per_strategy[key] = {
            "job_id": sd.job_id, "template_key": sd.template_key,
            "folds": _fold_table(sd),
            "diagnostics": sd.diagnostics,
        }
    return {
        "notice": "descriptive comparison only; no ranking, winner, or recommendation across strategies",
        "strategies": per_strategy,
    }
