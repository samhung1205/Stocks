"""Pure OOS diagnostics across walk-forward folds (no DB, no engine).

Everything here is DESCRIPTIVE.  With at most a handful of folds no significance test, confidence
interval or p-value is computed or implied; dispersion is min/median/max/mean of the folds that
exist.  Stitched numbers are never mixed into independent-fold aggregates.
"""
from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence

from app.services import warning_contract

DIAGNOSTICS_VERSION = "wf-diagnostics/f1/1"
COMPARE_KEYS = ("sharpe", "cagr", "max_drawdown", "turnover")
COST_KEYS = ("turnover", "cost_drag_pct", "net_return", "gross_return")


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _stats(values: Sequence) -> dict | None:
    xs = [float(v) for v in values if _finite(v)]
    if not xs:
        return None
    return {"n": len(xs), "min": min(xs), "median": statistics.median(xs), "max": max(xs),
            "mean": statistics.fmean(xs), "std": statistics.pstdev(xs) if len(xs) > 1 else 0.0}


def primary_edge_axes(primary_params: Mapping, topo: Mapping) -> list[str]:
    """Search axes (>= 3 values) on which the frozen PRIMARY sits at the min or max value.

    Same threshold as the IS-peak check in ``robustness`` but evaluated on the medoid, which is what
    is actually traded.  Weight-group members count when the weight is at its lowest/highest value
    among the group's combinations (>= 3 distinct values).  ``topo`` = ``search_space.topology``.
    """
    out = []
    for path, values in (topo.get("axes") or {}).items():
        if len(values) >= 3 and primary_params.get(path) in (values[0], values[-1]):
            out.append(path)
    return out


def group_edge_axes(primary_params: Mapping, points: Sequence[Mapping], topo: Mapping) -> list[str]:
    out = []
    for gpath, cfg in (topo.get("groups") or {}).items():
        for key in cfg["keys"]:
            flat = f"{gpath}.{key}"
            values = sorted({p["params"][flat] for p in points if flat in p["params"]})
            if len(values) >= 3 and primary_params.get(flat) in (values[0], values[-1]):
                out.append(flat)
    return out


def jaccard(a: Sequence[str], b: Sequence[str]) -> float | None:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else None


def is_vs_oos(is_metrics: Mapping | None, oos_metrics: Mapping | None) -> dict:
    """IS training summary of the frozen primary vs its independent OOS fold."""
    out = {}
    for key in COMPARE_KEYS:
        i, o = (is_metrics or {}).get(key), (oos_metrics or {}).get(key)
        entry = {"is": i, "oos": o, "delta": (o - i) if _finite(i) and _finite(o) else None}
        if key == "sharpe":
            entry["ratio"] = (o / i) if _finite(i) and _finite(o) and i > 0 else None
        out[key] = entry
    return out


def analyze(folds: Sequence[Mapping], plan: Mapping) -> dict:
    """``folds``: per-fold dicts {index, status, outcome, truncated, plateau_label, edge_axes,
    primary_edge_axes, primary_params, primary_key, region_keys, candidate_digest, is_metrics,
    oos_metrics, unresolved_action_count, partial_rebalance_count, max_severity, severity_counts,
    training_max_severity, error}.  ``plan``: {planned, dropped, notes}."""
    done = [f for f in folds if f["status"] == "completed"]
    evaluated = [f for f in done if f["outcome"] == "evaluated"]
    no_candidate = [f for f in done if f["outcome"] == "no_candidate"]
    failed = [f for f in folds if f["status"] == "failed"]

    comparison = []
    for f in evaluated:
        comparison.append({"fold": f["index"], **is_vs_oos(f["is_metrics"], f["oos_metrics"])})
    degradation = {}
    for key in COMPARE_KEYS:
        deltas = [c[key]["delta"] for c in comparison]
        degradation[key] = {"delta_oos_minus_is": _stats(deltas),
                            "folds_oos_lower": sum(1 for c in comparison if _finite(c[key]["delta"])
                                                   and c[key]["delta"] < 0)}
    degradation["sharpe"]["folds_oos_negative"] = sum(
        1 for c in comparison if _finite(c["sharpe"]["oos"]) and c["sharpe"]["oos"] < 0)

    # parameter / region stability across evaluated folds
    axes = sorted({p for f in evaluated for p in (f["primary_params"] or {})})
    per_axis = {}
    for axis in axes:
        vals = [f["primary_params"].get(axis) for f in evaluated]
        per_axis[axis] = {"values": vals, "distinct": len({repr(v) for v in vals}),
                          "changes": sum(1 for a, b in zip(vals, vals[1:]) if a != b)}
    pairs = list(zip(evaluated, evaluated[1:]))
    stability = {
        "n_evaluated_folds": len(evaluated),
        "primary_keys": [f["primary_key"] for f in evaluated],
        "primary_changes": sum(1 for a, b in pairs if a["primary_key"] != b["primary_key"]),
        "candidate_digests": [f["candidate_digest"] for f in evaluated],
        "candidate_digest_changes": sum(1 for a, b in pairs if a["candidate_digest"] != b["candidate_digest"]),
        "region_jaccard_consecutive": [{"folds": [a["index"], b["index"]],
                                        "jaccard": jaccard(a["region_keys"], b["region_keys"])}
                                       for a, b in pairs],
        "region_sizes": [len(f["region_keys"]) for f in evaluated],
        "per_axis": per_axis,
        "note": "consecutive evaluated folds only; no_candidate folds have no frozen primary"}
    plateau = {
        "labels": {f["index"]: f["plateau_label"] for f in done},
        "folds_with_is_peak_on_edge": [f["index"] for f in evaluated if f["edge_axes"]],
        "folds_with_primary_on_edge": [f["index"] for f in evaluated if f["primary_edge_axes"]],
        "primary_edge_axes": {f["index"]: f["primary_edge_axes"] for f in evaluated if f["primary_edge_axes"]}}
    cost = {"per_fold": [{"fold": f["index"], **{k: (f["oos_metrics"] or {}).get(k) for k in COST_KEYS},
                          "is_turnover": (f["is_metrics"] or {}).get("turnover")} for f in evaluated],
            "dispersion": {k: _stats([(f["oos_metrics"] or {}).get(k) for f in evaluated]) for k in COST_KEYS}}
    counts = {"unresolved_action_count": {f["index"]: f["unresolved_action_count"] for f in evaluated},
              "unresolved_action_total": sum(f["unresolved_action_count"] or 0 for f in evaluated),
              "partial_rebalance_count": {f["index"]: f["partial_rebalance_count"] for f in evaluated},
              "partial_rebalance_total": sum(f["partial_rebalance_count"] or 0 for f in evaluated)}
    severity = {"per_fold": {f["index"]: {"max_severity": f["max_severity"], "counts": f["severity_counts"],
                                          "training_max_severity": f.get("training_max_severity")}
                             for f in done},
                "max_severity": warning_contract.max_severity(
                    [f["max_severity"] for f in done if f["max_severity"]] or []) if done else None}
    completeness = {"planned_folds": plan["planned"], "completed": len(done), "evaluated": len(evaluated),
                    "no_candidate": len(no_candidate), "failed": len(failed),
                    "pending": plan["planned"] - len(done) - len(failed),
                    "truncated_final_fold": any(f["truncated"] for f in folds),
                    "dropped_windows": plan.get("dropped", []), "plan_notes": plan.get("notes", []),
                    "complete": len(done) == plan["planned"],
                    "evaluated_fraction": (len(evaluated) / plan["planned"]) if plan["planned"] else None,
                    "no_candidate_folds": [f["index"] for f in no_candidate]}
    dispersion = {"descriptive_only": True, "n_folds": len(evaluated),
                  "statistical_inference": "not performed",
                  "note": ("At most a handful of folds: min/median/max/mean describe these folds only; "
                           "no test, interval or significance claim is made."),
                  "independent_fold_metrics": {k: _stats([(f["oos_metrics"] or {}).get(k) for f in evaluated])
                                               for k in ("net_return", "cagr", "sharpe", "max_drawdown", "turnover")},
                  "no_candidate_folds_counted_as_cash": len(no_candidate)}
    flags = [warning_contract.structural("FOLD_DISPERSION_DESCRIPTIVE",
                                         f"{len(evaluated)} evaluated fold(s); descriptive only")]
    if no_candidate:
        flags.append(warning_contract.structural(
            "NO_STABLE_CANDIDATE", f"{len(no_candidate)} fold(s) had no stable candidate; held cash"))
    return {"version": DIAGNOSTICS_VERSION, "evidence": "independent_fold",
            "is_vs_oos": comparison, "degradation": degradation, "parameter_stability": stability,
            "plateau_edge": plateau, "cost_stability": cost, "counts": counts, "severity": severity,
            "completeness": completeness, "fold_dispersion": dispersion, "flags": flags}
