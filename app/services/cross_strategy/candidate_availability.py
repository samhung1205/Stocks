"""Stage G Task 6: candidate availability complementarity across strategies. Pure.

Only describes what happened (which strategies had a stable candidate each fold). The
"illustrative cash range" is explicitly NOT a Portfolio Lab allocation decision — it is one
arithmetic example (equal sleeve weights) of what a no_candidate -> cash rule would imply,
offered so Stage H has a concrete number to react to, not a recommendation.
"""
from __future__ import annotations

from typing import Mapping

from .data import StrategyData


def analyze(strategies: Mapping[str, StrategyData]) -> dict:
    n_total = len(strategies)
    fold_indices = sorted({idx for sd in strategies.values() for idx in sd.folds})
    per_fold = []
    for idx in fold_indices:
        evaluated, no_candidate, missing = [], [], []
        for key, sd in sorted(strategies.items()):
            fold = sd.folds.get(idx)
            if fold is None:
                missing.append(key)
            elif fold.outcome == "evaluated":
                evaluated.append(key)
            else:
                no_candidate.append(key)
        active = len(evaluated)
        equal_weight = 1.0 / n_total if n_total else 0.0
        per_fold.append({
            "fold": idx,
            "evaluated": evaluated, "no_candidate": no_candidate, "missing_from_job": missing,
            "active_strategy_count": active, "total_strategies": n_total,
            "all_active_no_candidate": active == 0 and bool(no_candidate) and not missing,
            "illustrative_equal_weight_cash_fraction":
                round((n_total - active) * equal_weight, 6) if n_total else None,
            "illustrative_note": "equal sleeve weights, no_candidate -> cash — one arithmetic "
                                 "example, not a Portfolio Lab allocation rule",
        })
    any_all_nc = any(f["all_active_no_candidate"] for f in per_fold)
    cash_fractions = [f["illustrative_equal_weight_cash_fraction"] for f in per_fold
                      if f["illustrative_equal_weight_cash_fraction"] is not None]
    return {
        "per_fold": per_fold,
        "any_fold_all_strategies_no_candidate": any_all_nc,
        "illustrative_cash_fraction_range": [min(cash_fractions), max(cash_fractions)] if cash_fractions else None,
    }
