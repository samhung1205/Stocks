"""Candidate freeze: the only bridge from Optimization to Walk Forward.

Contract (see docs/history/stage-f0-walkforward-contract-report.md):

1. A freeze is derived from ONE completed optimization job whose research window
   is exactly the fold's training window.  That job's panel ends at the training
   end, so no test-window row can influence it (tested by mutating later data).
2. The freeze rule is fixed and versioned (``RULE_VERSION``) and reads only that
   job's in-sample results.  It picks a *region* and one pre-declared ``primary``
   point; the test window evaluates the primary and reports the region as a set.
   Nothing is ever re-selected after test data is seen.
3. Rows are write-once (SQLite triggers).  ``open_test_window`` stamps the first
   moment test data is touched; after that, the training job's revision chain is
   closed — search-space expansion must start a NEW research (new root), never
   a revision of a job whose OOS result has been observed.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Mapping

from sqlalchemy import insert, update

from app.db import query_all, query_one
from app.db import tables as t
from app.services import optimization_lab as opt
from app.services import warning_contract
from app.services.optimization import robustness

RULE_VERSION = "wf-freeze/f0.5/2"
_hash, _dumps, _loads, _now = opt._hash, opt._dumps, opt._loads, opt._now

# surface label -> structural warning that must travel with a frozen candidate
LABEL_WARNINGS = {"isolated_peak": "ISOLATED_PEAK", "scattered": "SCATTERED_SURFACE",
                  "flat": "FLAT_SURFACE", "insufficient_data": "INSUFFICIENT_ROBUSTNESS_DATA"}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def region_medoid(region_keys: list[str], graph: Mapping[str, list[str]], by_key: Mapping[str, Mapping],
                  higher_is_better: bool = True) -> tuple[str, dict]:
    """Deterministic representative of a plateau region (pure).

    Minimise the sum of graph distances (BFS over the region-induced neighbour graph) to the other
    region points; ties -> lower turnover (missing = worst), then better objective, then point_key.
    Never reads anything but in-sample training results.  The IS peak is NOT special.
    """
    keys = sorted(region_keys)
    inside = set(keys)
    if len(keys) == 1:
        return keys[0], {keys[0]: 0}
    unreachable = len(keys)      # region is connected by construction; guard anyway
    totals: dict[str, int] = {}
    for src in keys:
        dist, frontier = {src: 0}, [src]
        while frontier:
            nxt = []
            for u in frontier:
                for v in graph.get(u, ()):
                    if v in inside and v not in dist:
                        dist[v] = dist[u] + 1
                        nxt.append(v)
            frontier = nxt
        totals[src] = sum(dist.get(k, unreachable) for k in keys if k != src)

    def order(k):
        m = by_key[k].get("metrics") or {}
        turnover = m.get("turnover")
        value = by_key[k].get("value")
        score = (-float(value) if higher_is_better else float(value)) if _finite(value) else math.inf
        return (totals[k], float(turnover) if _finite(turnover) else math.inf, score, k)
    return min(keys, key=order), totals


def select_candidates(analysis: Mapping, points: list[Mapping], graph: Mapping[str, list[str]] | None = None,
                      *, require_plateau: bool = True, higher_is_better: bool = True) -> dict:
    """Pure freeze rule (``wf-freeze/f0.5/2``) over an in-sample robustness analysis.

    ``plateau``  -> region = full plateau region; primary = region MEDOID (not the IS peak).
    otherwise    -> not freezable by default (``NO_STABLE_CANDIDATE``); with
                    ``require_plateau=False`` the single top point is frozen; the surface label
                    and its warning travel with it (``warnings``).
    """
    plateau = analysis["plateau"]
    label = plateau["label"]
    by_key = {p["key"]: p for p in points}
    out = {"rule_version": RULE_VERSION, "plateau_label": label,
           "edge_axes": list(plateau.get("edge_of_search_range") or []),
           "primary_key": None, "region": [], "freezable": False, "reasons": [],
           "is_peak_key": plateau.get("peak_key"), "medoid_distance_sums": None}
    if label == "plateau" and plateau["peak_key"] in by_key:
        keys = sorted(k for k in plateau["region_keys"] if k in by_key)
        primary, totals = region_medoid(keys, graph or {}, by_key, higher_is_better)
        out.update(primary_key=primary, freezable=True, medoid_distance_sums=totals)
    elif not require_plateau and plateau.get("peak_key") in by_key:
        keys = [plateau["peak_key"]]
        out.update(primary_key=plateau["peak_key"], freezable=True,
                   reasons=[f"FROZEN_WITHOUT_PLATEAU:{label}"])
    else:
        out["reasons"] = ["NO_STABLE_CANDIDATE", f"LABEL:{label}"]
        return out
    out["region"] = [{"key": k, "params": by_key[k]["params"], "value": by_key[k]["value"]} for k in keys]
    out["warnings"] = freeze_warnings(label, out["edge_axes"])
    return out


def freeze_warnings(label: str | None, edge_axes: list[str]) -> list[dict]:
    """Severity items that must accompany a frozen candidate (derived, so not stored separately)."""
    items = []
    if label != "plateau":
        items.append(warning_contract.structural("FROZEN_WITHOUT_PLATEAU", f"surface label: {label}"))
        if label in LABEL_WARNINGS:
            items.append(warning_contract.structural(LABEL_WARNINGS[label]))
    if edge_axes:
        items.append(warning_contract.structural("EDGE_OF_SEARCH_RANGE", ",".join(edge_axes)))
    return items


def candidate_digest(template_key: str, objective: str, sel: Mapping) -> str:
    """Content hash of what was frozen: independent of job ids and timestamps."""
    return _hash({"rule": sel["rule_version"], "template": template_key, "objective": objective,
                  "primary": sel["primary_key"],
                  "region": [{"key": r["key"], "params": r["params"],
                              "value": None if r["value"] is None else round(float(r["value"]), 10)}
                             for r in sel["region"]]})


def _view(row: Mapping) -> dict:
    return {**{k: v for k, v in row.items()
               if k not in ("primary_params_json", "region_json", "edge_axes_json")},
            "primary_params": _loads(row["primary_params_json"]),
            "region": _loads(row["region_json"]), "edge_axes": _loads(row["edge_axes_json"]),
            "test_opened": row["test_opened_at"] is not None,
            "warnings": freeze_warnings(row["plateau_label"], _loads(row["edge_axes_json"]))}


def get_freeze(freeze_id: str) -> dict:
    with opt.engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM wf_candidate_freezes WHERE id=:i", i=freeze_id)
    if not row:
        raise LookupError(f"candidate freeze {freeze_id} not found")
    return _view(row)


def freeze_from_job(job_id: str, fold: Mapping | None = None, *, require_plateau: bool = True) -> dict:
    """Freeze the candidate(s) of a completed training job (idempotent per job)."""
    with opt.engine.connect() as conn:
        existing = query_one(conn, "SELECT * FROM wf_candidate_freezes WHERE job_id=:j", j=job_id)
        if existing:
            return {**_view(existing), "created": False}
        job = opt._job_row(conn, job_id)
    if job["status"] != "completed":
        raise ValueError(f"only a completed training job can be frozen (status={job['status']})")
    request = _loads(job["request_json"])
    if fold is not None:
        if (request["research_start"], request["research_end"]) != (fold["train_start"], fold["train_end"]):
            raise ValueError(
                "TRAIN_WINDOW_MISMATCH: job research window "
                f"{request['research_start']}..{request['research_end']} != fold training window "
                f"{fold['train_start']}..{fold['train_end']}; a job that saw other dates cannot be frozen for this fold")
        if request["research_end"] >= fold["test_start"]:
            raise ValueError("training window must end before the test window starts")
    job, view, request, rows, done, points, topo, (name, key, higher) = opt._analysis_inputs(job_id, None)
    analysis = robustness.analyze(points, topo, higher_is_better=higher)
    valid = [p for p in points if _finite(p.get("value"))]
    graph = robustness.neighbour_graph(valid, topo)
    sel = select_candidates(analysis, points, graph, require_plateau=require_plateau,
                            higher_is_better=higher)
    if not sel["freezable"]:
        raise ValueError(f"{sel['reasons'][0]}: {', '.join(sel['reasons'][1:])}")
    primary = next(r for r in sel["region"] if r["key"] == sel["primary_key"])
    row = {
        "id": uuid.uuid4().hex, "job_id": job_id, "root_job_id": job["root_job_id"] or job_id,
        "template_key": job["template_key"], "strategy_id": job["strategy_id"],
        "strategy_version_id": job["strategy_version_id"], "definition_hash": job["definition_hash"],
        "objective": job["objective"], "train_start": request["research_start"],
        "train_end": request["research_end"], "rule_version": RULE_VERSION,
        "plateau_label": sel["plateau_label"], "primary_point_key": sel["primary_key"],
        "primary_params_json": _dumps(primary["params"]), "region_json": _dumps(sel["region"]),
        "edge_axes_json": _dumps(sel["edge_axes"]),
        "candidate_digest": candidate_digest(job["template_key"], job["objective"], sel),
        "created_at": _now()}
    with opt.engine.begin() as conn:
        conn.execute(insert(t.wf_candidate_freezes).values(**row))
    return {**get_freeze(row["id"]), "created": True, "reasons": sel["reasons"]}


def open_test_window(freeze_id: str) -> dict:
    """Stamp the first access to test data (write-once).  F1 must call this BEFORE loading any
    row dated after the freeze's ``train_end``; it closes the revision chain of the training job."""
    with opt.engine.begin() as conn:
        conn.execute(update(t.wf_candidate_freezes).where(
            (t.wf_candidate_freezes.c.id == freeze_id)
            & (t.wf_candidate_freezes.c.test_opened_at.is_(None))).values(test_opened_at=_now()))
    return get_freeze(freeze_id)
