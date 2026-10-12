"""Parameter-surface robustness analysis (pure, deterministic).

Everything here is descriptive and in-sample.  There is intentionally no
"best"/"winner" output: the objective only orders points for exploration, and a
point is judged by how its *neighbourhood* behaves.

Topology
--------
Each scalar axis is an ordered list; a point's coordinate on it is the index.
A weight group contributes its unit vector (weight / step).  Distance between
two points = sum of index differences on scalar axes + (L1 distance of the
unit vectors) / 2 per group, i.e. moving one weight unit from one key to
another costs 1.  **Neighbours = distance exactly 1.**

Plateau rule (all thresholds returned in ``rule``)
--------------------------------------------------
Let ``score`` = objective (sign-flipped when lower is better) and ``spread`` =
max(score) - min(score) over valid points.

1. ``insufficient_data`` if valid points < ``min_points`` or the top point has
   no neighbour with a valid score.
2. ``flat`` if ``spread`` <= ``flat_tol`` (relative to the score scale): the
   objective does not discriminate between points.
3. ``peak_drop`` = (score(top) - median(score of its neighbours)) / spread.
   ``isolated_peak`` if ``peak_drop`` > ``peak_drop_tol``.
4. Upper set U = the ``max(min_region_size, ceil(top_fraction * n_valid))``
   highest scores (ties broken by point key).  The peak region is the
   connected component of U (neighbour edges) containing the top point.
   ``plateau`` if the region has >= ``min_region_size`` points.
5. otherwise ``scattered``.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Any, Iterable, Mapping, Sequence

RULE = {
    "top_fraction": 0.25, "min_points": 6, "min_region_size": 3,
    "peak_drop_tol": 0.25, "flat_tol": 1e-9,
    "neighbour_distance": 1,
}

REPORTED_METRICS = ("cagr", "sharpe", "sortino", "max_drawdown", "calmar", "net_return",
                    "gross_return", "annualized_volatility", "turnover", "cost_drag_pct",
                    "unresolved_action_count", "warnings_count")


# ── numeric helpers ─────────────────────────────────────
def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated quantile (type 7)."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def dispersion(values: Iterable[Any]) -> dict:
    xs = [float(v) for v in values if _finite(v)]
    if not xs:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None,
                "mean": None, "std": None, "iqr": None, "range": None, "cv": None}
    mean = statistics.fmean(xs)
    std = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    p25, p75 = quantile(xs, .25), quantile(xs, .75)
    return {"n": len(xs), "min": min(xs), "p25": p25, "median": quantile(xs, .5), "p75": p75,
            "max": max(xs), "mean": mean, "std": std, "iqr": p75 - p25, "range": max(xs) - min(xs),
            "cv": std / abs(mean) if abs(mean) > 1e-12 else None}


def _avg_ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _avg_ranks(xs), _avg_ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


# ── topology ────────────────────────────────────────────
def _coords(params: Mapping, topo: Mapping) -> tuple | None:
    out: list = []
    for path, values in topo["axes"].items():
        if path not in params or params[path] not in values:
            return None
        out.append(values.index(params[path]))
    for gpath, cfg in topo["groups"].items():
        units = []
        for k in cfg["keys"]:
            w = params.get(f"{gpath}.{k}")
            if w is None:
                return None
            units.append(round(w / cfg["step"]))
        out.append(tuple(units))
    return tuple(out)


def _distance(a: tuple, b: tuple) -> int:
    d = 0.0
    for x, y in zip(a, b):
        d += sum(abs(i - j) for i, j in zip(x, y)) / 2 if isinstance(x, tuple) else abs(x - y)
    return round(d)


def neighbour_graph(points: Sequence[Mapping], topo: Mapping) -> dict[str, list[str]]:
    """key -> neighbour keys (distance 1).  Points without coordinates are isolated."""
    coords = {p["key"]: _coords(p["params"], topo) for p in points}
    keys = sorted(coords)
    graph: dict[str, list[str]] = {k: [] for k in keys}
    for i, a in enumerate(keys):
        if coords[a] is None:
            continue
        for b in keys[i + 1:]:
            if coords[b] is not None and _distance(coords[a], coords[b]) == 1:
                graph[a].append(b)
                graph[b].append(a)
    return graph


def _sign(higher_is_better: bool) -> int:
    return 1 if higher_is_better else -1


# ── analysis ────────────────────────────────────────────
def analyze(points: Sequence[Mapping], topo: Mapping, *, higher_is_better: bool = True,
            rule: Mapping | None = None) -> dict:
    """Neighbour sensitivity, axis sensitivity, plateau classification.

    ``points``: ``{"key", "params" (flat), "value" (objective or None), "metrics"}``.
    """
    r = {**RULE, **(rule or {})}
    sign = _sign(higher_is_better)
    valid = [p for p in points if _finite(p.get("value"))]
    score = {p["key"]: sign * float(p["value"]) for p in valid}
    graph = neighbour_graph(valid, topo)
    n = len(valid)
    spread = (max(score.values()) - min(score.values())) if n else 0.0
    scale = max(1.0, max((abs(v) for v in score.values()), default=0.0))
    flat_surface = n > 0 and spread <= r["flat_tol"] * scale

    per_point = []
    for p in sorted(valid, key=lambda q: q["key"]):
        nb = [score[k] for k in graph[p["key"]]]
        own = score[p["key"]]
        med = statistics.median(nb) if nb else None
        drop = None if (med is None or flat_surface or spread == 0) else (own - med) / spread
        per_point.append({
            "key": p["key"], "value": p["value"], "neighbour_count": len(nb),
            "neighbour_median": None if med is None else sign * med,
            "neighbour_min": None if not nb else sign * (min(nb) if sign == 1 else max(nb)),
            "neighbour_max": None if not nb else sign * (max(nb) if sign == 1 else min(nb)),
            "neighbour_mean_abs_diff": None if not nb else statistics.fmean(abs(own - x) for x in nb),
            "local_drop": drop,   # >0: higher than neighbours (spike-like); normalized by spread
            "stable": None if drop is None else drop <= r["peak_drop_tol"],
        })

    ordered = sorted(valid, key=lambda p: (-score[p["key"]], p["key"]))
    upper_size = min(n, max(r["min_region_size"], math.ceil(r["top_fraction"] * n))) if n else 0
    upper = [p["key"] for p in ordered[:upper_size]]
    plateau = _classify(ordered, score, graph, upper, spread, flat_surface, n, r)
    plateau["upper_set"] = upper
    plateau["upper_set_size"] = len(upper)
    plateau["edge_of_search_range"] = _boundary_axes(ordered[0], topo) if ordered else []

    region_keys = set(plateau["region_keys"]) or set(upper)
    by_key = {p["key"]: p for p in valid}
    return {
        "n_points": len(points), "n_valid": n, "spread": spread if n else None,
        "rule": dict(r), "plateau": plateau, "points": per_point,
        "axis_sensitivity": _axis_sensitivity(valid, score, graph, topo, spread, sign),
        "top_set_dispersion": _region_dispersion([by_key[k] for k in upper]),
        "peak_region_dispersion": (_region_dispersion([by_key[k] for k in plateau["region_keys"]])
                                   if plateau["region_keys"] else None),
        "top_set_parameters": _region_parameters([by_key[k] for k in sorted(region_keys)]),
    }


def _classify(ordered, score, graph, upper, spread, flat_surface, n, r) -> dict:
    out = {"label": None, "reasons": [], "peak_key": None, "peak_drop": None,
           "region_keys": [], "region_size": 0}
    if not ordered:
        out.update(label="insufficient_data", reasons=["NO_VALID_POINTS"])
        return out
    peak = ordered[0]["key"]
    out["peak_key"] = peak
    nb = [score[k] for k in graph[peak] if k in score]
    if n < r["min_points"]:
        out.update(label="insufficient_data", reasons=["TOO_FEW_POINTS"])
        return out
    if not nb:
        out.update(label="insufficient_data", reasons=["PEAK_HAS_NO_NEIGHBOURS"])
        return out
    if flat_surface:
        out.update(label="flat", reasons=["OBJECTIVE_DOES_NOT_DISCRIMINATE"])
        return out
    drop = (score[peak] - statistics.median(nb)) / spread
    out["peak_drop"] = drop
    region = _component(peak, set(upper), graph)
    out["region_keys"] = sorted(region)
    out["region_size"] = len(region)
    if drop > r["peak_drop_tol"]:
        out.update(label="isolated_peak", reasons=["PEAK_DROP_EXCEEDS_TOL"])
    elif len(region) >= r["min_region_size"]:
        out.update(label="plateau", reasons=["CONNECTED_TOP_REGION"])
    else:
        out.update(label="scattered", reasons=["TOP_REGION_TOO_SMALL"])
    return out


def _component(start: str, allowed: set[str], graph: Mapping[str, list[str]]) -> set[str]:
    if start not in allowed:
        return set()
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in graph.get(queue.popleft(), ()):
            if nxt in allowed and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _boundary_axes(peak: Mapping, topo: Mapping) -> list[str]:
    """Scalar axes (>=3 values) on which the top point sits at the min or max value."""
    out = []
    for path, values in topo["axes"].items():
        if len(values) >= 3 and peak["params"].get(path) in (values[0], values[-1]):
            out.append(path)
    return out


def _axis_sensitivity(valid, score, graph, topo, spread, sign) -> dict:
    by_key = {p["key"]: p for p in valid}
    out: dict[str, dict] = {}
    scalar_paths = list(topo["axes"])
    for path in scalar_paths:
        diffs = []
        for a, nbs in graph.items():
            for b in nbs:
                if a < b and a in by_key and b in by_key:
                    pa, pb = by_key[a]["params"], by_key[b]["params"]
                    if pa.get(path) != pb.get(path):
                        diffs.append(abs(score[a] - score[b]))
        out[path] = {"kind": "axis", "step_pairs": len(diffs),
                     "mean_abs_step_change": statistics.fmean(diffs) if diffs else None,
                     "normalized": (statistics.fmean(diffs) / spread) if diffs and spread else None}
    for gpath, cfg in topo["groups"].items():
        keys = [f"{gpath}.{k}" for k in cfg["keys"]]
        diffs = []
        for a, nbs in graph.items():
            for b in nbs:
                if a < b and a in by_key and b in by_key:
                    pa, pb = by_key[a]["params"], by_key[b]["params"]
                    if any(pa.get(k) != pb.get(k) for k in keys):
                        diffs.append(abs(score[a] - score[b]))
        out[gpath] = {"kind": "weights", "step_pairs": len(diffs),
                      "mean_abs_step_change": statistics.fmean(diffs) if diffs else None,
                      "normalized": (statistics.fmean(diffs) / spread) if diffs and spread else None}
    flat_paths = scalar_paths + [f"{g}.{k}" for g, c in topo["groups"].items() for k in c["keys"]]
    for path in flat_paths:
        marginal: dict[Any, list[float]] = {}
        for p in valid:
            if path in p["params"]:
                marginal.setdefault(p["params"][path], []).append(sign * score[p["key"]])
        means = {str(k): statistics.fmean(v) for k, v in sorted(marginal.items(), key=lambda kv: str(kv[0]))}
        target = out.setdefault(path, {"kind": "weight_key", "step_pairs": None,
                                       "mean_abs_step_change": None, "normalized": None})
        target["marginal_mean"] = means
        vals = list(means.values())
        target["marginal_range"] = (max(vals) - min(vals)) if vals else None
        target["marginal_range_normalized"] = ((max(vals) - min(vals)) / spread
                                               if vals and spread else None)
    ranking = sorted((k for k, v in out.items() if v["kind"] != "weight_key"
                      and v["normalized"] is not None),
                     key=lambda k: (-out[k]["normalized"], k))
    return {"by_parameter": out, "ranking_by_step_sensitivity": ranking}


def _region_dispersion(rows: Sequence[Mapping]) -> dict:
    return {"size": len(rows),
            "metrics": {m: dispersion(r.get("metrics", {}).get(m) for r in rows)
                        for m in REPORTED_METRICS}}


def _region_parameters(rows: Sequence[Mapping]) -> dict:
    paths = sorted({p for r in rows for p in r["params"]})
    out = {}
    for path in paths:
        counts: dict[Any, int] = {}
        for r in rows:
            v = r["params"].get(path)
            counts[v] = counts.get(v, 0) + 1
        top_v, top_n = max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))
        out[path] = {"values": {str(k): v for k, v in sorted(counts.items(), key=lambda kv: str(kv[0]))},
                     "modal_value": top_v, "modal_share": top_n / len(rows)}
    return out


# ── run-level diagnostics ───────────────────────────────
def diagnostics(rows: Sequence[Mapping], objective_values: Sequence[float | None],
                higher_is_better: bool = True) -> dict:
    """Cost / turnover / unresolved-action / coverage diagnostics across runs."""
    metrics = [r.get("metrics", {}) for r in rows]
    turnover_pairs = [(o, m.get("turnover")) for o, m in zip(objective_values, metrics)
                      if _finite(o) and _finite(m.get("turnover"))]
    unresolved = [m.get("unresolved_action_count") for m in metrics]
    with_unres = sum(1 for u in unresolved if _finite(u) and u > 0)
    sel_min = [m.get("selected_min") for m in metrics]
    return {
        "n_runs": len(rows),
        "turnover": dispersion(m.get("turnover") for m in metrics),
        "cost_drag_pct": dispersion(m.get("cost_drag_pct") for m in metrics),
        "unresolved_action_count": dispersion(unresolved),
        "runs_with_unresolved_actions": with_unres,
        "share_runs_with_unresolved_actions": (with_unres / len(rows)) if rows else None,
        "warnings_count": dispersion(m.get("warnings_count") for m in metrics),
        "selected_count_min": dispersion(sel_min),
        "objective_turnover_spearman": spearman([a for a, _ in turnover_pairs],
                                                [b for _, b in turnover_pairs]),
        "note": ("A strongly positive objective/turnover correlation means higher-ranked points "
                 "also trade more, so cost assumptions matter more for them."),
    }


# ── parameter surface ───────────────────────────────────
_AGGS = {
    "median": statistics.median, "mean": statistics.fmean,
    "min": min, "max": max, "count": len,
}


def surface(points: Sequence[Mapping], topo: Mapping, x: str, y: str | None = None,
            agg: str = "median") -> dict:
    """2-D (or 1-D) projection of the objective.  Other axes are aggregated, and
    min/max/n per cell are always returned so the aggregation cannot hide spread."""
    if agg not in _AGGS:
        raise ValueError(f"agg must be one of {sorted(_AGGS)}")
    flat_paths = {p for p in topo["axes"]} | {f"{g}.{k}" for g, c in topo["groups"].items()
                                              for k in c["keys"]}
    for label, path in (("x", x), ("y", y)):
        if path is not None and path not in flat_paths:
            raise ValueError(f"unknown {label} parameter: {path}")
    if y is not None and x == y:
        raise ValueError("x and y must differ")

    def order(path: str) -> list:
        if path in topo["axes"]:
            return list(topo["axes"][path])
        return sorted({p["params"][path] for p in points if path in p["params"]})

    valid = [p for p in points if _finite(p.get("value"))]
    cells: dict[tuple, list[float]] = {}
    for p in valid:
        if x in p["params"] and (y is None or y in p["params"]):
            cells.setdefault((p["params"][x], p["params"].get(y) if y else None), []).append(float(p["value"]))
    xs = order(x)
    ys = order(y) if y else [None]
    out_cells = []
    for xv in xs:
        for yv in ys:
            vals = cells.get((xv, yv))
            if vals:
                out_cells.append({"x": xv, "y": yv, "value": _AGGS[agg](vals),
                                  "n": len(vals), "min": min(vals), "max": max(vals)})
    return {"x": {"path": x, "values": xs}, "y": {"path": y, "values": ys if y else []},
            "agg": agg, "cells": out_cells,
            "aggregated_over": sorted(flat_paths - {x, y}),
            "points": [{"params": p["params"], "value": p["value"]} for p in points]}
