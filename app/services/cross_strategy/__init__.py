"""Stage G: cross-strategy analysis / complementarity.

Read-only analytical layer over existing Walk Forward evidence — see ``data.py`` for the DB
boundary (it re-reads ``walkforward_lab`` accessors; nothing is duplicated into a new table).

This module never ranks, scores, or recommends a strategy, and never computes a capital
allocation. It compares independent-fold OOS evidence descriptively across the strategies given
to it, ahead of a future Portfolio Lab stage.
"""
from __future__ import annotations

from typing import Mapping

from app.services import warning_contract

from . import behavior, candidate_availability, correlation, data, drawdown, exposure, overlap, sleeve

VERSION = "cross-strategy/g/1"

NOTICE = ("Descriptive cross-strategy comparison only. No ranking, winner, or capital allocation "
          "is computed here. Each Walk Forward job has at most a handful of OOS folds; no "
          "statistical significance, confidence interval, or frequency claim is made anywhere "
          "in this output.")


def _fold_window_check(strategies: Mapping[str, data.StrategyData]) -> dict:
    """All five templates share one protocol, so fold index k should mean the same calendar test
    window for every strategy. This is checked, not assumed — a mismatch is surfaced as a
    warning rather than silently correlating unrelated periods under the same fold index."""
    by_fold: dict[int, dict[str, list[str]]] = {}
    for key, sd in strategies.items():
        for idx, fold in sd.folds.items():
            by_fold.setdefault(idx, {})[key] = fold.windows["test"]
    mismatches = []
    windows = {}
    for idx, per_strategy in sorted(by_fold.items()):
        distinct = {tuple(w) for w in per_strategy.values()}
        windows[idx] = sorted(distinct)[0] if len(distinct) == 1 else None
        if len(distinct) > 1:
            mismatches.append({"fold": idx, "windows_seen": {k: v for k, v in per_strategy.items()}})
    return {"windows_by_fold": windows, "mismatches": mismatches}


def analyze(job_map: Mapping[str, str]) -> dict:
    """``job_map``: {strategy_key: job_id}, e.g. {"momentum_trend": "<job id>", ...}. Any subset
    of strategies may be passed; missing ones are reported, not fatal."""
    strategies, failed = data.load_all(job_map)
    warnings = []
    if failed:
        warnings.append(warning_contract.structural(
            "MISSING_STRATEGY_DATA", f"{len(failed)} of {len(job_map)} requested strategies unavailable: "
            f"{', '.join(sorted(failed))}"))
    if not strategies:
        return {"version": VERSION, "notice": NOTICE, "requested": list(job_map), "failed": failed,
                "warnings": warnings, "error": "no strategy data could be loaded"}

    fold_check = _fold_window_check(strategies)
    if fold_check["mismatches"]:
        warnings.append(warning_contract.structural(
            "FOLD_WINDOW_MISALIGNED", f"{len(fold_check['mismatches'])} fold index(es) do not share "
            "the same test window across strategies"))
    warnings.append(warning_contract.structural("CROSS_STRATEGY_DESCRIPTIVE_ONLY"))

    equity_by_strategy_fold = {
        key: {idx: fold.equity for idx, fold in sd.folds.items() if fold.outcome == "evaluated"}
        for key, sd in strategies.items()}

    monthly_holdings = {key: overlap.monthly_snapshots(sd) for key, sd in strategies.items()}
    all_stock_ids = {sid for snaps in monthly_holdings.values() for holdings in snaps.values() for sid in holdings}
    sectors = data.sector_map(all_stock_ids)

    return {
        "version": VERSION, "notice": NOTICE,
        "requested": list(job_map), "loaded": sorted(strategies), "failed": failed,
        "fold_windows": fold_check,
        "oos_behavior": behavior.compare(strategies),
        "correlation": {
            "full_sample": correlation.correlation_matrix(strategies),
            "downside": correlation.downside_correlation_matrix(strategies),
            "drawdown_period": correlation.drawdown_period_correlation_matrix(strategies),
        },
        "holdings_overlap": overlap.analyze(strategies),
        "exposure": exposure.analyze(monthly_holdings, sectors),
        "drawdown_complementarity": drawdown.complementarity(equity_by_strategy_fold),
        "candidate_availability": candidate_availability.analyze(strategies),
        "sleeve_contract": sleeve.build_all(strategies),
        "warnings": warnings,
    }
