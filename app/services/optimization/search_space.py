"""Search-space definition, validation and deterministic expansion.

Pure module.  A *parameter point* is a flat ``{dotted.path: value}`` dict; weight
groups are flattened to one path per key (``factor_weights.quality``).

Design rules (E1):

* Every template declares which parameters may be searched and their allowed
  range (``ALLOWED``).  Anything else is rejected, so a config cannot be
  brute-forced over knobs that have no research meaning.
* Weight groups are enumerated as *normalized discrete combinations*: integer
  unit vectors summing to ``1/step`` (each key >= ``min_weight``).  No two
  points are equivalent up to scale, and the sum is exactly 1.
* Grid larger than ``max_runs`` is sub-sampled with ``random.Random(seed)``;
  same seed + same space => same points, in grid order.
"""
from __future__ import annotations

import itertools
import json
import math
import random
from typing import Any, Iterable, Mapping

HARD_MAX_RUNS = 200
DEFAULT_MAX_RUNS = 60
DEFAULT_SEED = 0


def _int(lo: int, hi: int) -> dict:
    return {"type": "int", "min": lo, "max": hi}


def _group(keys: tuple[str, ...], step: float = 0.1, min_weight: float = 0.1) -> dict:
    return {"type": "weights", "keys": list(keys), "step": step, "min_weight": min_weight}


_COMMON = {
    "selection.top_n": _int(10, 100),
    "rebalance.frequency": {"type": "enum", "values": ["monthly", "weekly"]},
}
_QUALITY = {"quality_weights": _group(("roe", "cash_conversion", "gross_margin", "low_debt"))}

# Ranges are bounded by the research window: the panel has ~275 sessions of
# warm-up before the first 2022-11-01 rebalance, so lookbacks above 252 cannot
# be computed at the start of the period.
ALLOWED: dict[str, dict[str, dict]] = {
    "quality_growth_momentum": {
        **_COMMON, **_QUALITY,
        "momentum.lookback": _int(63, 252), "momentum.skip_recent": _int(0, 42),
        "factor_weights": _group(("quality", "growth", "momentum")),
        "growth.weights": _group(("revenue_yoy", "financial_growth")),
    },
    "revenue_growth_acceleration": {
        **_COMMON,
        "revenue.acceleration_window": _int(2, 12),
        "revenue.weights": _group(("yoy", "acceleration")),
    },
    "momentum_trend": {
        **_COMMON,
        "momentum.lookback": _int(63, 252), "momentum.skip_recent": _int(0, 42),
        "trend.ma_lookback": _int(20, 250),
        "factor_weights": _group(("momentum", "trend")),
    },
    "value_quality": {
        **_COMMON, **_QUALITY,
        "value.max_valuation_age_days": _int(1, 30),
        "value.weights": _group(("earnings_yield", "book_yield")),
        "factor_weights": _group(("value", "quality")),
    },
    "low_volatility_quality": {
        **_COMMON, **_QUALITY,
        "volatility.lookback": _int(20, 252),
        "factor_weights": _group(("low_volatility", "quality")),
    },
}

# Conservative default spaces (<= 48 points each).  Chosen for research meaning,
# not to maximise any metric.  rebalance.frequency is searchable but excluded
# from the defaults: weekly runs cost ~4x and add mostly turnover.
DEFAULT_SPACES: dict[str, dict] = {
    "quality_growth_momentum": {
        "axes": {"momentum.lookback": [126, 252], "selection.top_n": [20, 30, 50]},
        "weight_groups": {"factor_weights": {"step": 0.2, "min_weight": 0.2}},
    },
    "revenue_growth_acceleration": {
        "axes": {"revenue.acceleration_window": [2, 3, 6], "selection.top_n": [20, 30, 50]},
        "weight_groups": {"revenue.weights": {"step": 0.2, "min_weight": 0.2}},
    },
    "momentum_trend": {
        "axes": {"momentum.lookback": [126, 189, 252], "momentum.skip_recent": [0, 21],
                 "trend.ma_lookback": [100, 200], "selection.top_n": [20, 30, 50]},
        "weight_groups": {},
    },
    "value_quality": {
        "axes": {"selection.top_n": [20, 30, 50]},
        "weight_groups": {"factor_weights": {"step": 0.2, "min_weight": 0.2},
                          "value.weights": {"step": 0.2, "min_weight": 0.2}},
    },
    "low_volatility_quality": {
        "axes": {"volatility.lookback": [63, 126, 252], "selection.top_n": [20, 30, 50]},
        "weight_groups": {"factor_weights": {"step": 0.2, "min_weight": 0.2}},
    },
}


# ── flat <-> nested ─────────────────────────────────────
def flatten(overrides: Mapping, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in overrides.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.update(flatten(value, path + "."))
        else:
            out[path] = value
    return out


def to_overrides(flat: Mapping[str, Any]) -> dict:
    """Nested params override dict from a flat point."""
    out: dict = {}
    for path, value in flat.items():
        node = out
        *parents, leaf = path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def point_key(flat: Mapping[str, Any]) -> str:
    return json.dumps({k: flat[k] for k in sorted(flat)}, separators=(",", ":"))


def _group_of(template_key: str, path: str) -> str | None:
    """Weight-group path a flat leaf path belongs to (or None)."""
    for gpath, spec in ALLOWED[template_key].items():
        if spec["type"] == "weights" and path.startswith(gpath + ".") \
                and path[len(gpath) + 1:] in spec["keys"]:
            return gpath
    return None


# ── validation ──────────────────────────────────────────
def _check_scalar(path: str, spec: Mapping, value: Any) -> Any:
    if spec["type"] == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"{path} must be an integer")
        value = int(value)
        if not spec["min"] <= value <= spec["max"]:
            raise ValueError(f"{path}={value} outside allowed range [{spec['min']}, {spec['max']}]")
        return value
    if spec["type"] == "enum":
        if value not in spec["values"]:
            raise ValueError(f"{path}={value!r} not in {spec['values']}")
        return value
    raise ValueError(f"{path} is a weight group; give it as a weight_groups entry")


def _units(spec: Mapping) -> tuple[int, int]:
    step, minimum = float(spec["step"]), float(spec["min_weight"])
    if not (0 < step <= 0.5) or minimum < 0:
        raise ValueError("weight group step must be in (0, .5] and min_weight >= 0")
    total = round(1 / step)
    if abs(total * step - 1) > 1e-9:
        raise ValueError(f"weight step {step} must divide 1 evenly")
    min_units = math.ceil(minimum / step - 1e-9)
    if min_units * len(spec["keys"]) > total:
        raise ValueError("weight group min_weight leaves no feasible combination")
    return total, min_units


def weight_combinations(keys: list[str], step: float, min_weight: float) -> list[dict[str, float]]:
    """All normalized discrete weight vectors, lexicographic in ``keys`` order."""
    total, min_units = _units({"keys": keys, "step": step, "min_weight": min_weight})
    out: list[dict[str, float]] = []

    def rec(i: int, left: int, acc: list[int]) -> None:
        if i == len(keys) - 1:
            if left >= min_units:
                units = acc + [left]
                out.append({k: round(u * step, 6) for k, u in zip(keys, units)})
            return
        for u in range(min_units, left - min_units * (len(keys) - i - 1) + 1):
            rec(i + 1, left - u, acc + [u])

    rec(0, total, [])
    return out


def normalize_space(template_key: str, space: Mapping | None = None) -> dict:
    """Validated canonical space: axes sorted, values sorted/deduped."""
    if template_key not in ALLOWED:
        raise ValueError(f"unknown strategy template: {template_key}")
    allowed = ALLOWED[template_key]
    raw = DEFAULT_SPACES[template_key] if space is None else space
    if not isinstance(raw, Mapping):
        raise ValueError("search_space must be an object")
    extra = set(raw) - {"axes", "weight_groups"}
    if extra:
        raise ValueError(f"unknown search_space key: {sorted(extra)[0]}")
    axes: dict[str, list] = {}
    for path, values in (raw.get("axes") or {}).items():
        if path not in allowed or allowed[path]["type"] == "weights":
            raise ValueError(f"parameter not searchable for {template_key}: {path}")
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError(f"axis {path} needs a non-empty list of values")
        checked = [_check_scalar(path, allowed[path], v) for v in values]
        if allowed[path]["type"] == "enum":
            order = allowed[path]["values"]
            axes[path] = sorted(set(checked), key=order.index)
        else:
            axes[path] = sorted(set(checked))
    groups: dict[str, dict] = {}
    for path, cfg in (raw.get("weight_groups") or {}).items():
        if path not in allowed or allowed[path]["type"] != "weights":
            raise ValueError(f"not a searchable weight group for {template_key}: {path}")
        if not isinstance(cfg, Mapping):
            raise ValueError(f"weight group {path} must be an object")
        spec = {**allowed[path], **{k: cfg[k] for k in ("step", "min_weight") if k in cfg}}
        _units(spec)
        groups[path] = {"keys": list(spec["keys"]), "step": float(spec["step"]),
                        "min_weight": float(spec["min_weight"])}
    if not axes and not groups:
        raise ValueError("search space is empty")
    return {"axes": {k: axes[k] for k in sorted(axes)},
            "weight_groups": {k: groups[k] for k in sorted(groups)}}


def default_space(template_key: str) -> dict:
    return normalize_space(template_key, None)


def _constraint_ok(flat: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    """momentum needs lookback > skip_recent (same rule as factors.momentum)."""
    look = flat.get("momentum.lookback", base.get("momentum.lookback"))
    skip = flat.get("momentum.skip_recent", base.get("momentum.skip_recent"))
    return look is None or skip is None or look > skip


def grid_size(space: Mapping) -> int:
    n = 1
    for values in space["axes"].values():
        n *= len(values)
    for cfg in space["weight_groups"].values():
        n *= len(weight_combinations(cfg["keys"], cfg["step"], cfg["min_weight"]))
    return n


def expand_grid(space: Mapping, base_flat: Mapping[str, Any] | None = None) -> tuple[list[dict], int]:
    """Full cartesian expansion in deterministic order -> (points, skipped_invalid)."""
    base_flat = base_flat or {}
    paths = list(space["axes"])
    group_paths = list(space["weight_groups"])
    group_options = [weight_combinations(c["keys"], c["step"], c["min_weight"])
                     for c in space["weight_groups"].values()]
    points, skipped = [], 0
    for scalars in itertools.product(*(space["axes"][p] for p in paths)):
        for combo in itertools.product(*group_options):
            flat = dict(zip(paths, scalars))
            for gpath, weights in zip(group_paths, combo):
                flat.update({f"{gpath}.{k}": v for k, v in weights.items()})
            if _constraint_ok(flat, base_flat):
                points.append(flat)
            else:
                skipped += 1
    return points, skipped


def normalize_explicit(template_key: str, combos: Iterable[Mapping]) -> list[dict]:
    """Validate explicit combinations (nested or flat dicts) -> deduped flat points."""
    if template_key not in ALLOWED:
        raise ValueError(f"unknown strategy template: {template_key}")
    allowed = ALLOWED[template_key]
    out, seen = [], set()
    for i, combo in enumerate(combos):
        if not isinstance(combo, Mapping) or not combo:
            raise ValueError(f"combination {i} must be a non-empty object")
        flat = flatten(combo)
        groups: dict[str, dict] = {}
        for path, value in list(flat.items()):
            gpath = _group_of(template_key, path)
            if gpath:
                groups.setdefault(gpath, {})[path[len(gpath) + 1:]] = flat.pop(path)
                continue
            if path not in allowed or allowed[path]["type"] == "weights":
                raise ValueError(f"combination {i}: parameter not searchable: {path}")
            flat[path] = _check_scalar(path, allowed[path], value)
        for gpath, weights in groups.items():
            keys = allowed[gpath]["keys"]
            if set(weights) != set(keys):
                raise ValueError(f"combination {i}: {gpath} needs exactly {keys}")
            vals = {}
            for k in keys:
                w = weights[k]
                if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= w <= 1:
                    raise ValueError(f"combination {i}: {gpath}.{k} must be within [0, 1]")
                vals[k] = round(float(w), 6)
            if abs(sum(vals.values()) - 1) > 1e-6:
                raise ValueError(f"combination {i}: {gpath} weights must sum to 1")
            flat.update({f"{gpath}.{k}": v for k, v in vals.items()})
        if not _constraint_ok(flat, {}):
            raise ValueError(f"combination {i}: momentum.lookback must exceed momentum.skip_recent")
        key = point_key(flat)
        if key not in seen:
            seen.add(key)
            out.append(flat)
    if not out:
        raise ValueError("combinations may not be empty")
    return out


def sample_points(points: list[dict], max_runs: int, seed: int) -> list[dict]:
    """Deterministic seeded subsample, returned in original grid order."""
    if len(points) <= max_runs:
        return list(points)
    picked = sorted(random.Random(seed).sample(range(len(points)), max_runs))
    return [points[i] for i in picked]


def plan(template_key: str, payload: Mapping, base_flat: Mapping[str, Any] | None = None) -> dict:
    """Resolve a job's search request into a concrete, budgeted list of points.

    payload keys: ``search_space`` (grid) **or** ``combinations`` (explicit),
    ``max_runs``, ``seed``, ``budget_policy`` ('sample' | 'reject').
    """
    max_runs = payload.get("max_runs", DEFAULT_MAX_RUNS)
    seed = payload.get("seed", DEFAULT_SEED)
    if isinstance(max_runs, bool) or not isinstance(max_runs, int) or not 1 <= max_runs <= HARD_MAX_RUNS:
        raise ValueError(f"max_runs must be an integer in [1, {HARD_MAX_RUNS}]")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    policy = payload.get("budget_policy", "sample")
    if policy not in ("sample", "reject"):
        raise ValueError("budget_policy must be sample or reject")
    if payload.get("combinations") is not None and payload.get("search_space") is not None:
        raise ValueError("give either search_space or combinations, not both")
    if payload.get("combinations") is not None:
        combos = payload["combinations"]
        if not isinstance(combos, (list, tuple)):
            raise ValueError("combinations must be a list")
        points = normalize_explicit(template_key, combos)
        if len(points) > max_runs:
            raise ValueError(f"{len(points)} explicit combinations exceed max_runs={max_runs}")
        return {"mode": "explicit", "space": None, "points": points, "grid_size": len(points),
                "skipped_invalid": 0, "sampled": False, "max_runs": max_runs, "seed": seed}
    space = normalize_space(template_key, payload.get("search_space"))
    points, skipped = expand_grid(space, base_flat)
    if not points:
        raise ValueError("search space produced no valid parameter points")
    total = len(points)
    sampled = total > max_runs
    if sampled and policy == "reject":
        raise ValueError(f"grid has {total} points, exceeding max_runs={max_runs}")
    return {"mode": "grid", "space": space, "points": sample_points(points, max_runs, seed),
            "grid_size": total, "skipped_invalid": skipped, "sampled": sampled,
            "max_runs": max_runs, "seed": seed}


def topology(template_key: str, space: Mapping | None, points: list[Mapping]) -> dict:
    """Ordered axes + weight groups used for neighbour relations.

    Grid mode uses the declared space.  Explicit mode infers axes from the
    values actually present (numeric ascending / enum in declared order).
    """
    allowed = ALLOWED[template_key]
    if space is not None:
        axes = {p: list(v) for p, v in space["axes"].items()}
        groups = {p: {"keys": list(c["keys"]), "step": c["step"]}
                  for p, c in space["weight_groups"].items()}
        return {"axes": axes, "groups": groups}
    axes, groups, scalar_seen = {}, {}, {}
    for flat in points:
        for path, value in flat.items():
            gpath = _group_of(template_key, path)
            if gpath:
                groups.setdefault(gpath, {"keys": list(allowed[gpath]["keys"]),
                                          "step": allowed[gpath]["step"]})
            else:
                scalar_seen.setdefault(path, set()).add(value)
    for path, values in scalar_seen.items():
        if allowed[path]["type"] == "enum":
            axes[path] = sorted(values, key=allowed[path]["values"].index)
        else:
            axes[path] = sorted(values)
    return {"axes": {k: axes[k] for k in sorted(axes)}, "groups": {k: groups[k] for k in sorted(groups)}}


def catalog() -> dict[str, dict]:
    """Allowed parameters + default space per template (API metadata)."""
    return {key: {"allowed": ALLOWED[key], "default_space": default_space(key),
                  "default_grid_size": grid_size(default_space(key)),
                  "hard_max_runs": HARD_MAX_RUNS, "default_max_runs": DEFAULT_MAX_RUNS}
            for key in ALLOWED}
