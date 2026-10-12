"""Stage H1: flatten one component's per-fold exact sleeves (H0's
``contract.build_exact_sleeve``) into a single date-indexed daily series a
portfolio research run can read like a normal time series, without knowing
anything about Walk Forward folds.
"""
from __future__ import annotations

from typing import Mapping

from .contract import build_exact_sleeve
from .data import StrategyData

SERIES_CONTRACT_VERSION = "portfolio-component-series/h1/1"


def build_component_series(sd: StrategyData, job_id: str) -> dict:
    """``{date: {stock_weight_by_stock, cash_weight, total_equity, daily_return,
    candidate_status, fold_index}}`` — one row per OOS trading date across every
    loaded (completed) fold, in fold order. Fold test windows never overlap (each
    independent fold starts fresh at its own ``test_start``), so a later fold's
    date never actually collides with an earlier one; if the underlying data ever
    did produce a collision, this reports it in ``date_collisions`` rather than
    silently letting the later fold overwrite the earlier one."""
    by_date: dict[str, dict] = {}
    collisions: list[str] = []
    for idx in sorted(sd.folds):
        sleeve = build_exact_sleeve(sd, idx, job_id)
        for row in sleeve["daily"]:
            d = row["date"]
            if d in by_date:
                collisions.append(d)
                continue
            by_date[d] = {
                "stock_weight_by_stock": row["stock_weight_by_stock"],
                "cash_weight": row["cash_weight"],
                "total_equity": row["total_equity"],
                "daily_return": row["daily_return"],
                "candidate_status": sleeve["candidate_status"],
                "fold_index": idx,
            }
    return {"contract_version": SERIES_CONTRACT_VERSION, "by_date": by_date,
            "dates": sorted(by_date), "date_collisions": collisions}


def common_trading_dates(series_by_component: Mapping[str, dict], start: str, end: str) -> list[str]:
    """Dates present in EVERY component's series and inside ``[start, end]`` —
    the portfolio can only be evaluated where every pinned component actually has
    OOS evidence (no fabricated exposure for a date a component's job never
    covered)."""
    if not series_by_component:
        return []
    common: set[str] | None = None
    for s in series_by_component.values():
        dates = {d for d in s["dates"] if start <= d <= end}
        common = dates if common is None else (common & dates)
    return sorted(common or set())


def prior_returns(series_by_component: Mapping[str, dict], as_of: str) -> dict[str, list[float]]:
    """Per-component list of ``daily_return`` values strictly before ``as_of``,
    in date order — the raw input a caller must still route through
    ``leakage.prior_only_returns``-style filtering (this already only includes
    ``date < as_of`` rows, matching that filter's semantics) before handing to
    ``allocation.inverse_volatility``."""
    out: dict[str, list[float]] = {}
    for key, s in series_by_component.items():
        rows = [s["by_date"][d]["daily_return"] for d in s["dates"] if d < as_of]
        out[key] = [r for r in rows if r is not None]
    return out
