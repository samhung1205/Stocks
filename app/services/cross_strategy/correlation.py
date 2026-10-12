"""Stage G Task 2: return correlation across strategies. Pure — operates on already-loaded
equity points (see ``data.py`` for how they're fetched).

Cash-restart handling: returns are computed per fold by ``drawdown.daily_returns`` (never
bridging a fold boundary — see that module's docstring). A strategy's full return series here
is the UNION, over its own evaluated folds only, of {date: return}. Two different fold indices
never share a calendar date (the protocol's test windows are consecutive, non-overlapping), so
merging per-fold date->return maps into one per-strategy dict cannot silently splice across a
boundary — pairing is always done by exact calendar date, and a date that exists for strategy A
only inside fold 2 can only match strategy B's date if B was also evaluated in fold 2 (same
window). A no-candidate (cash) fold contributes a constant-zero return series and is EXCLUDED
entirely: a zero-variance series makes correlation formally 0/0, and including it would silently
show a fake "0.0" correlation instead of "not computable".
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from .data import StrategyData
from .drawdown import daily_returns, in_drawdown_dates

MIN_OVERLAP = 5   # fewer common return dates than this: correlation withheld, not just noisy


def strategy_return_series(sd: StrategyData) -> dict[str, tuple[float, int]]:
    """{date: (return, fold_index)} unioned over evaluated folds; no-candidate folds excluded."""
    out: dict[str, tuple[float, int]] = {}
    for idx, fold in sd.folds.items():
        if fold.outcome != "evaluated":
            continue
        for date, ret in daily_returns(fold.equity).items():
            out[date] = (ret, idx)
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _pair_correlation(a: Mapping[str, tuple[float, int]], b: Mapping[str, tuple[float, int]],
                      date_filter=None) -> dict:
    dates = sorted(set(a) & set(b))
    if date_filter is not None:
        dates = [d for d in dates if d in date_filter]
    if len(dates) < MIN_OVERLAP:
        return {"correlation": None, "n_dates": len(dates), "folds": [],
                "reason": f"fewer than {MIN_OVERLAP} overlapping dates"}
    xs = [a[d][0] for d in dates]
    ys = [b[d][0] for d in dates]
    folds = sorted({a[d][1] for d in dates} | {b[d][1] for d in dates})
    return {"correlation": _pearson(xs, ys), "n_dates": len(dates), "folds": folds}


def _all_series(strategies: Mapping[str, StrategyData]) -> tuple[dict, list[str]]:
    """{key: return_series} for strategies with at least one evaluated-fold return, plus the
    sorted list of keys excluded for having none (all folds no_candidate, or none loaded)."""
    series = {k: strategy_return_series(sd) for k, sd in strategies.items()}
    non_empty = {k: v for k, v in series.items() if v}
    return non_empty, sorted(set(series) - set(non_empty))


def _matrix(series: Mapping[str, Mapping[str, tuple[float, int]]], excluded: list[str],
           date_filter=None) -> dict:
    keys = sorted(series)
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            pairs.append({"pair": [a, b], **_pair_correlation(series[a], series[b], date_filter)})
    return {"strategies": keys, "pairs": pairs,
            "degenerate_or_empty_series_excluded_from_input": excluded}


def correlation_matrix(strategies: Mapping[str, StrategyData]) -> dict:
    series, excluded = _all_series(strategies)
    result = _matrix(series, excluded)
    result["kind"] = "full_sample"
    result["min_overlap_required"] = MIN_OVERLAP
    return result


def _market_benchmark_returns(strategies: Mapping[str, StrategyData]) -> dict[str, float]:
    """Merged {date: benchmark_return}, built from whichever evaluated fold(s) recorded a
    benchmark value (identical across strategies sharing a fold — verified against the real
    F1.5 jobs; no-candidate folds record no benchmark)."""
    by_fold: dict[int, list[dict]] = {}
    for sd in strategies.values():
        for idx, fold in sd.folds.items():
            if fold.outcome == "evaluated" and fold.equity and idx not in by_fold:
                if all(p.get("benchmark") is not None for p in fold.equity):
                    by_fold[idx] = fold.equity
    out: dict[str, float] = {}
    for pts in by_fold.values():
        dates = sorted(p["date"] for p in pts)
        by_date = {p["date"]: p["benchmark"] for p in pts}
        for prev, cur in zip(dates, dates[1:]):
            if by_date[prev]:
                out[cur] = by_date[cur] / by_date[prev] - 1.0
    return out


def downside_correlation_matrix(strategies: Mapping[str, StrategyData]) -> dict:
    """Correlation restricted to dates where the shared market benchmark fell."""
    series, excluded = _all_series(strategies)
    bench = _market_benchmark_returns(strategies)
    down_dates = {d for d, r in bench.items() if r < 0}
    result = _matrix(series, excluded, date_filter=down_dates)
    result["kind"] = "downside"
    result["reference"] = "shared OOS market benchmark return < 0"
    result["down_dates_available"] = len(down_dates)
    result["min_overlap_required"] = MIN_OVERLAP
    return result


def drawdown_period_correlation_matrix(strategies: Mapping[str, StrategyData]) -> dict:
    """Correlation restricted to dates inside either strategy's OWN drawdown episode (>= 5%,
    see ``drawdown.DRAWDOWN_THRESHOLD``) for the fold that date belongs to."""
    series, excluded = _all_series(strategies)
    dd_dates = {k: set().union(*(in_drawdown_dates(f.equity) for f in sd.folds.values()
                                  if f.outcome == "evaluated")) if sd.folds else set()
                for k, sd in strategies.items()}
    keys = sorted(series)
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            window = dd_dates.get(a, set()) | dd_dates.get(b, set())
            pairs.append({"pair": [a, b], **_pair_correlation(series[a], series[b], window)})
    return {"kind": "drawdown_period", "strategies": keys, "pairs": pairs,
            "degenerate_or_empty_series_excluded_from_input": excluded,
            "min_overlap_required": MIN_OVERLAP,
            "note": "restricted to dates in either strategy's own >=5% drawdown episode"}
