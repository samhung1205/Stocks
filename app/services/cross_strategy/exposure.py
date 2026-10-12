"""Stage G Task 4: concentration and sector exposure across strategies. Pure — takes already
reconstructed monthly holdings (``overlap.monthly_snapshots``) and a sector lookup the caller
fetched once from ``data.sector_map``.

``stocks.industry`` is a CURRENT snapshot (see Stage C limitation), never a historical /
point-in-time taxonomy. Every sector number produced here carries that caveat forward instead
of presenting it as if it were the sector classification that applied on the holding's own
date — a 2024 holding may be shown under an industry the company only carries today.
"""
from __future__ import annotations

from typing import Mapping

from app.services import warning_contract

SECTOR_CAVEAT = warning_contract.structural(
    "CURRENT_SECTOR_TAXONOMY_ONLY",
    "sector shown is today's stocks.industry snapshot, not the holding-date taxonomy")


def _weights(holdings: Mapping[str, dict]) -> dict[str, float]:
    """Entry-notional weight where known, else an equal-weight fallback over the remaining
    (unknown-weight) names so every holding still contributes to concentration/sector totals."""
    known = {sid: h["weight"] for sid, h in holdings.items() if h.get("weight")}
    unknown = [sid for sid in holdings if sid not in known]
    if unknown:
        remaining = max(0.0, 1.0 - sum(known.values()))
        share = remaining / len(unknown) if remaining else 0.0
        known.update({sid: share for sid in unknown})
    return known


def concentration(holdings: Mapping[str, dict]) -> dict:
    weights = _weights(holdings)
    if not weights:
        return {"n_holdings": 0, "hhi": None, "top_weight": None}
    values = sorted(weights.values(), reverse=True)
    return {"n_holdings": len(weights), "hhi": sum(w * w for w in values),
            "top_weight": values[0], "top3_weight": sum(values[:3])}


def sector_exposure(holdings: Mapping[str, dict], sectors: Mapping[str, str | None]) -> dict[str, float]:
    weights = _weights(holdings)
    out: dict[str, float] = {}
    for sid, w in weights.items():
        sector = sectors.get(sid) or "unknown"
        out[sector] = out.get(sector, 0.0) + w
    return out


def sector_overlap(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Overlap coefficient: sum of min(weight) over sectors both hold. 0 = disjoint sector mix,
    1 = identical sector weighting."""
    return sum(min(a.get(s, 0.0), b.get(s, 0.0)) for s in set(a) | set(b))


def analyze(monthly_holdings: Mapping[str, Mapping[str, Mapping[str, dict]]],
           sectors: Mapping[str, str | None]) -> dict:
    """``monthly_holdings``: {strategy_key: {month: holdings}} (from ``overlap.monthly_snapshots``
    per strategy). Returns per-strategy per-month concentration + sector exposure, and per-pair
    per-month sector overlap."""
    keys = sorted(monthly_holdings)
    months = sorted({m for snaps in monthly_holdings.values() for m in snaps})
    per_strategy_month = []
    for key in keys:
        for month, holdings in sorted(monthly_holdings[key].items()):
            per_strategy_month.append({
                "strategy": key, "month": month,
                "concentration": concentration(holdings),
                "sector_exposure": sector_exposure(holdings, sectors),
            })
    pair_overlap = []
    for month in months:
        present = {k: monthly_holdings[k][month] for k in keys if month in monthly_holdings[k]}
        pkeys = sorted(present)
        exposures = {k: sector_exposure(present[k], sectors) for k in pkeys}
        for i in range(len(pkeys)):
            for j in range(i + 1, len(pkeys)):
                a, b = pkeys[i], pkeys[j]
                pair_overlap.append({"month": month, "pair": [a, b],
                                     "sector_overlap_coefficient": sector_overlap(exposures[a], exposures[b])})
    return {"caveat": SECTOR_CAVEAT, "per_strategy_month": per_strategy_month,
            "sector_overlap_by_month": pair_overlap}
