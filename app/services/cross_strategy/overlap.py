"""Stage G Task 3: holding overlap across strategies. Pure — operates on already-loaded fill
trades and equity points (see ``data.py``).

Holdings are reconstructed from ``fill`` trades (buy adds shares, sell subtracts), never from
target weights the engine may have intended, because a partial/deferred rebalance (see
``PARTIAL_REBALANCE`` / ``EXECUTION_DEFERRED`` in ``warning_contract``) can make the intended
target differ from what was actually filled. A holding's weight is approximated from the
notional of its most recent BUY fill divided by that day's gross equity ("weight at entry");
it is not re-marked to market as the price drifts, since no daily target-weight series is
recorded by the engine. This is stated wherever a weight number is reported.

"Common rebalance date" is read per calendar MONTH (a fill's ``date[:7]``), not by exact date,
because ``missing.execution=partial`` can shift one strategy's fills by a session or more
relative to another's for the same intended monthly rebalance (see the F1.5 report's
`partial_rebalance_count` / `EXECUTION_DEFERRED`).
"""
from __future__ import annotations

from typing import Mapping, Sequence

from .data import StrategyData


def holdings_by_date(fold_equity: Sequence[Mapping], fills: Sequence[Mapping]) -> dict[str, dict[str, dict]]:
    """{date: {stock_id: {"shares": n, "weight": last_buy_notional / equity_that_day | None}}}
    after applying that date's fills, cumulative from the fold's start (flat)."""
    equity_by_date = {p["date"]: p.get("gross_equity") for p in fold_equity}
    by_date: dict[str, list[Mapping]] = {}
    for f in fills:
        by_date.setdefault(f["date"], []).append(f)
    dates = sorted(by_date)
    shares: dict[str, float] = {}
    weight_basis: dict[str, float] = {}   # stock_id -> weight at its last buy
    snapshots: dict[str, dict[str, dict]] = {}
    for d in dates:
        for f in by_date[d]:
            sid, sh = f["stock_id"], f["shares"]
            delta = sh if f["side"] == "buy" else -sh
            shares[sid] = shares.get(sid, 0.0) + delta
            if f["side"] == "buy":
                denom = equity_by_date.get(d)
                weight_basis[sid] = (f.get("reference_notional") / denom) if denom else None
            if abs(shares.get(sid, 0.0)) < 1e-9:
                shares.pop(sid, None)
                weight_basis.pop(sid, None)
        snapshots[d] = {sid: {"shares": shares[sid], "weight": weight_basis.get(sid)} for sid in shares}
    return snapshots


def monthly_snapshots(sd: StrategyData) -> dict[str, dict[str, dict]]:
    """{month "YYYY-MM": holdings} — last snapshot date of each month, unioned over the
    strategy's own evaluated folds (disjoint calendar windows, so months never collide)."""
    out: dict[str, dict[str, dict]] = {}
    for fold in sd.folds.values():
        if fold.outcome != "evaluated" or not fold.fills:
            continue
        snaps = holdings_by_date(fold.equity, fold.fills)
        for date in sorted(snaps):
            out[date[:7]] = snaps[date]
    return out


def pairwise_jaccard(a: Mapping[str, dict], b: Mapping[str, dict]) -> float | None:
    sa, sb = set(a), set(b)
    union = sa | sb
    return len(sa & sb) / len(union) if union else None


def analyze(strategies: Mapping[str, StrategyData]) -> dict:
    monthly = {k: monthly_snapshots(sd) for k, sd in strategies.items()}
    months = sorted({m for snaps in monthly.values() for m in snaps})
    keys = sorted(strategies)
    per_month = []
    for month in months:
        present = {k: monthly[k][month] for k in keys if month in monthly[k]}
        if len(present) < 1:
            continue
        pair_rows = []
        pkeys = sorted(present)
        for i in range(len(pkeys)):
            for j in range(i + 1, len(pkeys)):
                a, b = pkeys[i], pkeys[j]
                shared = set(present[a]) & set(present[b])
                pair_rows.append({
                    "pair": [a, b], "jaccard": pairwise_jaccard(present[a], present[b]),
                    "overlap_count": len(shared),
                    "a_holding_count": len(present[a]), "b_holding_count": len(present[b]),
                    "common_stock_exposure_a": sum((present[a][s]["weight"] or 0.0) for s in shared) or None,
                    "common_stock_exposure_b": sum((present[b][s]["weight"] or 0.0) for s in shared) or None,
                })
        all_common = set.intersection(*(set(v) for v in present.values())) if present else set()
        selection_count: dict[str, int] = {}
        for holdings in present.values():
            for sid in holdings:
                selection_count[sid] = selection_count.get(sid, 0) + 1
        per_month.append({
            "month": month, "strategies_with_holdings": pkeys,
            "pairwise": pair_rows,
            "common_to_all_present": sorted(all_common),
            "common_to_all_present_note": f"intersection over the {len(present)} strategy(ies) "
                                          f"with holdings this month, not necessarily all 5",
            "selection_count_by_stock": dict(sorted(selection_count.items(), key=lambda kv: (-kv[1], kv[0]))),
        })
    return {"notice": "descriptive holdings overlap; weight is an entry-notional approximation, "
                      "not marked to market — see module docstring",
            "months": per_month}
