"""Stage G Task 5 (+ drawdown-period support for Task 2): per-fold drawdown episodes and
cross-strategy drawdown complementarity. Pure — operates on already-loaded equity points.

Drawdown is always computed WITHIN one fold's own date-ordered equity curve. Folds are never
concatenated for this purpose: each independent fold restarts from cash and pays its own entry
cost (see ``walkforward/stitched.py`` and the F1.5/F2 reports), so a "running peak" that crossed
a fold boundary would silently treat the cash-restart discontinuity as a real portfolio move.
"""
from __future__ import annotations

from typing import Mapping, Sequence

DRAWDOWN_THRESHOLD = -0.05   # an episode must reach at least -5% to count as a "drawdown period"


def equity_by_date(points: Sequence[Mapping]) -> dict[str, float]:
    return {p["date"]: p["net_equity"] for p in points if p.get("net_equity") is not None}


def daily_returns(points: Sequence[Mapping]) -> dict[str, float]:
    """Simple daily returns from one fold's own equity curve. The first date has no prior value
    inside this fold and is intentionally omitted (never inferred from starting capital, and never
    computed against the previous fold's last value)."""
    eq = equity_by_date(points)
    dates = sorted(eq)
    out = {}
    for prev, cur in zip(dates, dates[1:]):
        if eq[prev]:
            out[cur] = eq[cur] / eq[prev] - 1.0
    return out


def drawdown_curve(points: Sequence[Mapping]) -> dict[str, float]:
    """Running drawdown (<=0) from the peak seen so far, restarted at the fold's own first date."""
    eq = equity_by_date(points)
    dates = sorted(eq)
    curve = {}
    peak = None
    for d in dates:
        peak = eq[d] if peak is None else max(peak, eq[d])
        curve[d] = (eq[d] / peak - 1.0) if peak else 0.0
    return curve


def episodes(points: Sequence[Mapping], threshold: float = DRAWDOWN_THRESHOLD) -> list[dict]:
    """Contiguous peak->trough->(recovery|fold end) episodes reaching at least ``threshold``.

    Each: {peak_date, trough_date, trough_dd, recovery_date (None if not recovered inside the
    fold), recovered}. ``recovered`` false means the fold ended still below its own peak — with
    OOS test windows of ~6 months this is common and is not itself an anomaly.
    """
    curve = drawdown_curve(points)
    dates = sorted(curve)
    out: list[dict] = []
    i = 0
    n = len(dates)
    while i < n:
        if curve[dates[i]] < 0:
            start = i
            trough_i = i
            while i < n and curve[dates[i]] < 0:
                if curve[dates[i]] < curve[dates[trough_i]]:
                    trough_i = i
                i += 1
            recovered = i < n  # loop stopped because dd hit 0, not because data ran out
            if curve[dates[trough_i]] <= threshold:
                out.append({
                    "peak_date": dates[start - 1] if start > 0 else dates[start],
                    "trough_date": dates[trough_i], "trough_dd": curve[dates[trough_i]],
                    "recovery_date": dates[i] if recovered else None, "recovered": recovered,
                    "dates": dates[start:i]})
        else:
            i += 1
    return out


def in_drawdown_dates(points: Sequence[Mapping], threshold: float = DRAWDOWN_THRESHOLD) -> set[str]:
    out: set[str] = set()
    for ep in episodes(points, threshold):
        out.update(ep["dates"])
    return out


def complementarity(strategies: Mapping[str, Mapping[int, Sequence[Mapping]]]) -> dict:
    """``strategies``: {key: {fold_index: equity_points}} for EVALUATED folds only.

    Per fold, per pair of strategies both evaluated in it: whether their drawdown episodes'
    date ranges overlap, and what the other strategy's return was over that overlapping window.
    Purely descriptive; with <=4 folds no frequency claim ("usually", "typically") is made."""
    fold_index_all = sorted({idx for f in strategies.values() for idx in f})
    per_fold = []
    for idx in fold_index_all:
        present = {k: pts[idx] for k, pts in strategies.items() if idx in pts}
        if len(present) < 2:
            per_fold.append({"fold": idx, "strategies_evaluated": sorted(present), "pairs": []})
            continue
        eps = {k: episodes(pts) for k, pts in present.items()}
        rets = {k: daily_returns(pts) for k, pts in present.items()}
        keys = sorted(present)
        pairs = []
        for a_i in range(len(keys)):
            for b_i in range(a_i + 1, len(keys)):
                a, b = keys[a_i], keys[b_i]
                pairs.append({
                    "pair": [a, b],
                    "a_episode_count": len(eps[a]), "b_episode_count": len(eps[b]),
                    "a_drawdowns_b_behavior": _concurrent_behavior(eps[a], eps[b], rets[b]),
                    "b_drawdowns_a_behavior": _concurrent_behavior(eps[b], eps[a], rets[a]),
                })
        per_fold.append({"fold": idx, "strategies_evaluated": keys, "pairs": pairs})
    return {"descriptive_only": True, "threshold": DRAWDOWN_THRESHOLD,
            "note": "no frequency/probability claim; each fold is a single independent observation",
            "per_fold": per_fold}


def _concurrent_behavior(own_episodes: list[dict], other_episodes: list[dict],
                         other_returns: Mapping[str, float]) -> list[dict]:
    """For each of ``own_episodes``, the other strategy's compounded return over that exact date
    window, and whether the other strategy was ALSO in one of its own drawdown episodes at the
    same time (coincident) or not (complementary in this single instance)."""
    out = []
    for ep in own_episodes:
        window = set(ep["dates"])
        coincident = any(window & set(oe["dates"]) for oe in other_episodes)
        out.append({
            "own_episode": {"peak_date": ep["peak_date"], "trough_date": ep["trough_date"],
                            "trough_dd": ep["trough_dd"], "recovered": ep["recovered"]},
            "other_return_over_window": _compound(other_returns, sorted(window)),
            "other_also_in_drawdown": coincident,
        })
    return out


def _compound(returns: Mapping[str, float], dates: list[str]) -> float | None:
    vals = [returns[d] for d in dates if d in returns]
    if not vals:
        return None
    total = 1.0
    for r in vals:
        total *= (1.0 + r)
    return total - 1.0
