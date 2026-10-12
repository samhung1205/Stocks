"""Strategy Lab Stage E1: optimization jobs (persistence, lifecycle, cache, results).

Sits above the unchanged Stage B/C/D engine.  For one job:

* the *monthly universe union* is resolved once and frozen,
* the point-in-time panel (with price-only adjusted closes for factors; raw
  prices for execution/accounting) is built **at most once** and reused for
  every parameter point,
* each point runs ``run_backtest`` exactly as a normal Strategy Lab run would,
  and only afterwards are metrics recomputed over the fixed research window
  (the warm-up before the window is discarded so points with different
  lookbacks are compared over the same dates),
* results are exploratory in-sample orderings, never a recommendation.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
import traceback
import uuid
from typing import Any, Mapping

from sqlalchemy import insert, update

from app.db import engine, query_all, query_one
from app.db import tables as t
from app.services import data_foundation
from app.services import strategy_lab as lab
from app.services import warning_contract
from app.services.optimization import robustness, search_space
from app.services.strategy import metrics as strategy_metrics
from app.services.strategy import run_backtest
from app.services.strategy.panel import build_panel
from app.services.strategy.signals import TemplateSignalRule, _deep_merge
from app.services.strategy.templates import TEMPLATES

OPT_VERSION = "optimization/e1/1"
PANEL_WARMUP_START = "2021-09-22"   # first date with complete full-market quotes
RESEARCH_START, RESEARCH_END = "2022-11-01", "2026-09-18"
DEFAULT_UNIVERSE = {"min_avg_amount": 30_000_000, "min_observations": 120}
FUNDAMENTAL_TEMPLATES = frozenset(TEMPLATES) - {"momentum_trend"}
TERMINAL = ("completed", "failed", "cancelled")
NOTICE = ("Exploratory, in-sample ordering over the research window. It is not a forecast, not a "
          "recommendation and not investment advice: a candidate parameter region still needs "
          "out-of-sample validation (not part of E1).")

# name -> (metrics key, higher_is_better)
OBJECTIVES: dict[str, tuple[str, bool]] = {
    "sharpe": ("sharpe", True), "sortino": ("sortino", True), "calmar": ("calmar", True),
    "cagr": ("cagr", True), "net_return": ("net_return", True),
    "total_return": ("total_return", True), "max_drawdown": ("max_drawdown", True),
    "annualized_volatility": ("annualized_volatility", False),
    "turnover": ("turnover", False), "cost_drag": ("cost_drag_pct", False),
}
SUMMARY_KEYS = ("cagr", "sharpe", "sortino", "max_drawdown", "calmar", "net_return",
                "gross_return", "cost_drag_pct", "turnover", "annualized_volatility")
_now, _dumps, _loads, _hash, _clean = lab._now, lab._dumps, lab._loads, lab._hash, lab._clean


def _objective(name: str | None, default: str = "sharpe") -> tuple[str, str, bool]:
    name = name or default
    if name not in OBJECTIVES:
        raise ValueError(f"objective must be one of {sorted(OBJECTIVES)}")
    key, higher = OBJECTIVES[name]
    return name, key, higher


def sort_runs(rows: list[Mapping], objective: str) -> list[dict]:
    """Objective ordering for exploration: direction-aware, missing values last,
    ties broken by run_index so the order is deterministic."""
    _, key, higher = _objective(objective)

    def sort_key(row):
        v = (row.get("metrics") or {}).get(key)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        return (0, -v if higher else v, row["run_index"]) if ok else (1, 0.0, row["run_index"])
    return sorted((dict(r) for r in rows), key=sort_key)


# ── universe ────────────────────────────────────────────
def monthly_universe_union(conn, start: str, end: str, min_avg_amount: float,
                           min_observations: int) -> tuple[list[str], dict]:
    """Union of ``liquid_universe`` evaluated at the first trading day of each month
    in [start, end] (never a single frozen as_of)."""
    dates = [r["date"] for r in query_all(
        conn, "SELECT date FROM trading_calendar WHERE date BETWEEN :s AND :e ORDER BY date",
        s=start, e=end)]
    if not dates:
        dates = [r["date"] for r in query_all(
            conn, "SELECT DISTINCT date FROM daily_quotes WHERE date BETWEEN :s AND :e ORDER BY date",
            s=start, e=end)]
    firsts: dict[str, str] = {}
    for d in dates:
        firsts.setdefault(d[:7], d)
    union: set[str] = set()
    months = []
    for as_of in firsts.values():
        ids = {r["stock_id"] for r in data_foundation.liquid_universe(
            conn, as_of, min_avg_amount, min_observations)}
        months.append({"as_of": as_of, "count": len(ids), "new_vs_previous_months": len(ids - union)})
        union |= ids
    if not union:
        raise ValueError("monthly liquid universe is empty for the research window; backfill data "
                         "first or pass stock_ids")
    return sorted(union), {
        "kind": "monthly_union", "min_avg_amount": min_avg_amount,
        "min_observations": min_observations, "months": months,
        "union_size": len(union),
        "first_month_size": months[0]["count"] if months else 0,
        "note": ("Union of per-month liquid universes (trailing year before each month's first "
                 "trading day). Templates still apply their own daily liquidity filter, so "
                 "membership in the union is not membership in a portfolio. stocks table is a "
                 "current snapshot: survivorship limits apply.")}


# ── windowing ───────────────────────────────────────────
def windowed_metrics(result: Mapping, start: str, risk_free_rate: float = 0.0) -> dict:
    """Recompute the Stage B metric set over dates >= ``start`` (engine output untouched)."""
    curve = [p for p in result["equity_curve"] if p["date"] >= start]
    if len(curve) < 2:
        raise ValueError(f"research window starting {start} has fewer than 2 equity points")
    fills = [f for f in result["fills"] if f["date"] >= start]
    closed = [c for c in result["closed_trades"] if c["exit_date"] >= start]
    return strategy_metrics.compute(curve, closed, fills, risk_free_rate) | {
        "_curve_points": len(curve), "_fills": len(fills), "_closed": len(closed)}


class _Recorder:
    """Transparent SignalRule wrapper that records selection sizes; returns the signal untouched."""

    def __init__(self, rule, window_start: str):
        self.rule, self.start = rule, window_start
        self.selected: list[int] = []
        self.scored: list[int] = []

    def __call__(self, context):
        signal = self.rule(context)
        if context.date >= self.start:
            self.selected.append(len(getattr(signal, "candidates", ()) or ()))
            self.scored.append(len(getattr(signal, "scores", ()) or ()))
        return signal


def _median(xs: list[int]) -> float | None:
    return robustness.quantile(xs, .5) if xs else None


def evaluate_point(panel, template_key: str, resolved: Mapping, config, start: str) -> tuple[dict, dict]:
    """Run one parameter point on an already-built panel -> (metrics, diagnostics)."""
    recorder = _Recorder(TemplateSignalRule(template_key, resolved, None), start)
    result = run_backtest(panel, recorder, config)
    win = windowed_metrics(result, start, config.risk_free_rate)
    meta = result["metadata"]
    in_window = [u for u in meta["unresolved_actions"] if u["date"] >= start]
    fills = [f for f in result["fills"] if f["date"] >= start]
    gross, net = win.get("gross_return"), win.get("net_return")
    metrics = {k: v for k, v in win.items() if not k.startswith("_")}
    metrics.update({
        "cost_drag_pct": gross - net if gross is not None and net is not None else None,
        "unresolved_action_count": meta["unresolved_action_count"],
        "unresolved_action_count_in_window": len(in_window),
        "warnings_count": len(result["warnings"]),
        "selected_min": min(recorder.selected) if recorder.selected else None,
    })
    diagnostics = {
        "unresolved_action_count": meta["unresolved_action_count"],
        "unresolved_action_count_in_window": len(in_window),
        "unresolved_actions_sample": meta["unresolved_actions"][:10],
        "warnings_count": len(result["warnings"]),
        "warnings": result["warnings"][:50],
        "publication_mode": meta["publication_mode"],
        "coverage": {
            "window_equity_points": win["_curve_points"],
            "fills_in_window": win["_fills"], "closed_trades_in_window": win["_closed"],
            "first_fill_date": fills[0]["date"] if fills else None,
            "distinct_stocks_traded": len({f["stock_id"] for f in fills}),
            "fills_before_window": len(result["fills"]) - len(fills),
            "signals_in_window": len(recorder.selected),
            "signals_empty": sum(1 for n in recorder.selected if n == 0),
            "selected_min": min(recorder.selected) if recorder.selected else None,
            "selected_median": _median(recorder.selected),
            "selected_max": max(recorder.selected) if recorder.selected else None,
            "scored_median": _median(recorder.scored),
        },
    }
    return _clean(metrics), _clean(diagnostics)


# ── job creation ────────────────────────────────────────
def _iso(value: Any, name: str) -> str:
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from exc


def catalog() -> dict:
    return {"templates": search_space.catalog(),
            "objectives": {k: {"metric": m, "higher_is_better": h} for k, (m, h) in OBJECTIVES.items()},
            "research_window": {"start": RESEARCH_START, "end": RESEARCH_END,
                                "panel_start": PANEL_WARMUP_START},
            "notice": NOTICE}


def _base_flat(resolved: Mapping) -> dict:
    return search_space.flatten(resolved)


def space_diff(old: Mapping | None, new: Mapping | None) -> dict:
    """Axis/weight-group differences between two grid spaces (pure)."""
    old, new = old or {}, new or {}
    axes: dict[str, dict] = {}
    for path in sorted(set(old.get("axes") or {}) | set(new.get("axes") or {})):
        a, b = (old.get("axes") or {}).get(path, []), (new.get("axes") or {}).get(path, [])
        added, removed = [v for v in b if v not in a], [v for v in a if v not in b]
        if added or removed:
            axes[path] = {"added": added, "removed": removed}
    groups = {}
    for path in sorted(set(old.get("weight_groups") or {}) | set(new.get("weight_groups") or {})):
        a, b = (old.get("weight_groups") or {}).get(path), (new.get("weight_groups") or {}).get(path)
        if a != b:
            groups[path] = {"old": a, "new": b}
    only_adds = (all(not d["removed"] for d in axes.values())
                 and all(d["old"] is not None and d["new"] is not None for d in groups.values()))
    return {"axes": axes, "weight_groups": groups,
            "is_pure_expansion": bool(axes or groups) and only_adds and not groups}


def _link_strategy(conn, payload: Mapping, key: str, params: Mapping, backtest: Mapping,
                   resolved: Mapping) -> dict:
    """Immutable strategy provenance.  Only a version that really equals the submitted
    definition may be attributed; otherwise refuse instead of guessing."""
    own_hash = lab._definition_hash(key, params, backtest)
    out = {"strategy_id": None, "strategy_version_id": None, "strategy_version_no": None,
           "definition_hash": own_hash}
    sid, vid, vno = (payload.get(k) for k in ("strategy_id", "strategy_version_id", "strategy_version_no"))
    if sid is None and vid is None and vno is None:
        return out
    if sid is None or (vid is None and vno is None):
        raise ValueError("strategy provenance needs strategy_id and strategy_version_id (or strategy_version_no)")
    row = (query_one(conn, "SELECT * FROM strategy_versions WHERE id=:v AND strategy_id=:s", v=vid, s=sid)
           if vid is not None else
           query_one(conn, "SELECT * FROM strategy_versions WHERE version_no=:n AND strategy_id=:s", n=vno, s=sid))
    if not row:
        raise ValueError("strategy version not found for this strategy")
    if vid is not None and vno is not None and row["version_no"] != vno:
        raise ValueError("strategy_version_id and strategy_version_no disagree")
    if row["template_key"] != key:
        raise ValueError("strategy version template differs from template_key")
    v_params, v_backtest, v_resolved = lab.validate_definition(
        row["template_key"], _loads(row["params_json"]), _loads(row["backtest_json"]))
    same = (v_resolved == resolved and
            lab.build_backtest_config(v_backtest, key, v_params).to_dict()
            == lab.build_backtest_config(backtest, key, params).to_dict())
    if not same:
        raise ValueError("submitted definition differs from the referenced strategy version; "
                         "save it as a new version first")
    return {"strategy_id": row["strategy_id"], "strategy_version_id": row["id"],
            "strategy_version_no": row["version_no"], "definition_hash": row["definition_hash"]}


def _link_revision(conn, payload: Mapping, key: str, request: Mapping, prov: Mapping) -> dict:
    """Revision chain.  A revision changes ONLY the search space of a completed parent job over
    the identical window/definition; it never touches the parent and is refused once the
    parent chain's test window has been opened (no OOS-driven expansion)."""
    parent_id = payload.get("parent_job_id")
    if not parent_id:
        return {"parent_job_id": None, "root_job_id": None, "revision_no": 0,
                "revision_kind": None, "revision_reason": None, "revision_json": None}
    reason = str(payload.get("revision_reason") or "").strip()
    if not reason:
        raise ValueError("revision_reason is required for a search-space revision")
    parent = query_one(conn, "SELECT * FROM optimization_jobs WHERE id=:i", i=parent_id)
    if not parent:
        raise LookupError(f"parent optimization job {parent_id} not found")
    if parent["status"] != "completed":
        raise ValueError("a revision can only be derived from a completed job")
    if parent["template_key"] != key:
        raise ValueError("revision must use the parent's template")
    old = _loads(parent["request_json"])
    for field in ("research_start", "research_end", "panel_start", "publication_mode", "params",
                  "backtest", "objective", "benchmark_id", "universe_spec", "stock_ids"):
        if old.get(field) != request.get(field):
            raise ValueError(f"revision may only change the search space; {field} differs from parent "
                             "(a different window/definition/objective is a new research, not a revision)")
    for field in ("strategy_id", "strategy_version_id"):
        if parent[field] != prov[field]:
            raise ValueError(f"revision {field} differs from parent")
    root = parent["root_job_id"] or parent["id"]
    if chain_test_opened(conn, root):
        raise ValueError("TEST_WINDOW_ALREADY_OPENED: the parent's out-of-sample window has been "
                         "observed; expanding the search space now would be OOS-driven. Start a new "
                         "research (new root job) and treat it as a separate, disclosed trial")
    if old["search"]["mode"] != "grid" or request["search"]["mode"] != "grid":
        raise ValueError("search-space revision needs grid searches on both jobs")
    diff = space_diff(old["search"]["space"], request["search"]["space"])
    if not diff["axes"] and not diff["weight_groups"]:
        raise ValueError("revision does not change the search space")
    edge = []
    try:
        edge = get_robustness(parent_id)["analysis"]["plateau"].get("edge_of_search_range") or []
    except Exception:  # noqa: BLE001 - parent analysis is advisory here
        pass
    return {"parent_job_id": parent_id, "root_job_id": root,
            "revision_no": (parent["revision_no"] or 0) + 1, "revision_kind": "search_space_expansion",
            "revision_reason": reason,
            "revision_json": _dumps({"space_diff": diff, "parent_edge_axes": edge,
                                     "parent_search_grid_size": old["search"]["grid_size"]})}


def chain_test_opened(conn, root_job_id: str) -> bool:
    return bool(query_one(conn, "SELECT 1 x FROM wf_candidate_freezes WHERE root_job_id=:r "
                          "AND test_opened_at IS NOT NULL LIMIT 1", r=root_job_id))


def create_job(payload: Mapping) -> dict:
    """Validate, freeze the plan and queue a job.  Caller schedules ``execute_job``."""
    key = payload.get("template_key")
    if key not in TEMPLATES:
        raise ValueError(f"unknown strategy template: {key}")
    params, backtest, resolved = lab.validate_definition(key, payload.get("params"),
                                                         payload.get("backtest"))
    mode = payload.get("publication_mode", "assumed")
    if mode not in ("strict", "assumed"):
        raise ValueError("publication_mode must be strict or assumed")
    if mode == "strict" and key in FUNDAMENTAL_TEMPLATES:
        raise ValueError("fundamental templates are researched in assumed mode only "
                         "(strict-mode publication history does not cover the window)")
    start = _iso(payload.get("research_start", RESEARCH_START), "research_start")
    end = _iso(payload.get("research_end", RESEARCH_END), "research_end")
    if start >= end:
        raise ValueError("research_start must be before research_end")
    panel_start = _iso(payload.get("panel_start") or min(PANEL_WARMUP_START, start), "panel_start")
    if panel_start > start:
        raise ValueError("panel_start must not be after research_start")
    objective, _, _ = _objective(payload.get("objective"))
    plan = search_space.plan(key, payload, _base_flat(resolved))
    ids = payload.get("stock_ids")
    universe_spec = {**DEFAULT_UNIVERSE, **(payload.get("universe") or {})}
    if ids:
        if not isinstance(ids, (list, tuple)):
            raise ValueError("stock_ids must be a list")
        ids = sorted(dict.fromkeys(str(s) for s in ids))
    request = {
        "template_key": key, "params": params, "backtest": backtest,
        "research_start": start, "research_end": end, "panel_start": panel_start,
        "publication_mode": mode, "benchmark_id": payload.get("benchmark_id", "TAIEX_TR"),
        "universe_spec": universe_spec, "stock_ids": ids or None, "objective": objective,
        "search": {k: plan[k] for k in ("mode", "space", "grid_size", "skipped_invalid",
                                        "sampled", "max_runs", "seed")},
        "risk_free_rate": (lab.build_backtest_config(backtest, key, params).risk_free_rate),
    }
    points = plan["points"]
    with engine.begin() as conn:
        prov = _link_strategy(conn, payload, key, params, backtest, resolved)
        prov.update(_link_revision(conn, payload, key, request, prov))
    job_hash = _hash({"opt_version": OPT_VERSION, "request": {k: v for k, v in request.items()
                                                              if k != "objective"},
                      "points": [search_space.point_key(p) for p in points],
                      "strategy_version_id": prov["strategy_version_id"],
                      "parent_job_id": prov["parent_job_id"]})
    with engine.begin() as conn:
        hit = query_one(conn, """SELECT id,status FROM optimization_jobs WHERE job_hash=:h
            AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1""", h=job_hash)
        if hit:
            return {"job_id": hit["id"], "status": hit["status"], "queued": False,
                    "duplicate_of_active_job": True, "total_runs": len(points)}
        job_id = uuid.uuid4().hex
        prov["root_job_id"] = prov["root_job_id"] or job_id   # provenance is write-once: set at insert
        conn.execute(insert(t.optimization_jobs).values(
            id=job_id, template_key=key, status="queued", job_hash=job_hash, objective=objective,
            request_json=_dumps(request), total_runs=len(points), cancel_requested=0,
            retry_failed=0, panel_loads=0, created_at=_now(), **prov))
        conn.execute(insert(t.optimization_runs), [
            {"job_id": job_id, "run_index": i, "point_key": search_space.point_key(p),
             "params_json": _dumps(p), "status": "pending"} for i, p in enumerate(points)])
    return {"job_id": job_id, "status": "queued", "queued": True, "total_runs": len(points),
            "grid_size": plan["grid_size"], "sampled": plan["sampled"],
            "skipped_invalid": plan["skipped_invalid"], "max_runs": plan["max_runs"],
            "seed": plan["seed"]}


# ── execution ───────────────────────────────────────────
def _job_row(conn, job_id: str) -> dict:
    row = query_one(conn, "SELECT * FROM optimization_jobs WHERE id=:i", i=job_id)
    if not row:
        raise LookupError(f"optimization job {job_id} not found")
    return row


def _resolve_point(request: Mapping, flat: Mapping) -> tuple[dict, Any]:
    merged = _deep_merge(request["params"], search_space.to_overrides(flat))
    params, backtest, resolved = lab.validate_definition(request["template_key"], merged,
                                                         request["backtest"])
    return resolved, lab.build_backtest_config(backtest, request["template_key"], params)


def _run_hash(request: Mapping, resolved: Mapping, config, fingerprint: Mapping, ids_hash: str) -> str:
    return _hash({
        "opt_version": OPT_VERSION, "engine_version": lab.ENGINE_VERSION,
        "data_fingerprint": fingerprint, "template_key": request["template_key"],
        "template_config": resolved, "backtest_config": config.to_dict(),
        "panel_start": request["panel_start"], "research_start": request["research_start"],
        "research_end": request["research_end"], "stock_ids": ids_hash,
        "publication_mode": request["publication_mode"], "benchmark_id": request["benchmark_id"]})


def _cancel_requested(job_id: str) -> bool:
    with engine.connect() as conn:
        return bool(query_one(conn, "SELECT cancel_requested c FROM optimization_jobs WHERE id=:i",
                              i=job_id)["c"])


def _ensure_universe(job: Mapping, request: Mapping) -> list[str]:
    if request.get("stock_ids"):
        return list(request["stock_ids"])
    stored = _loads(job["universe_json"])
    if stored and stored.get("stock_ids"):
        return list(stored["stock_ids"])   # resume reuses the frozen universe
    spec = request["universe_spec"]
    with engine.connect() as conn:
        ids, info = monthly_universe_union(conn, request["research_start"], request["research_end"],
                                           spec["min_avg_amount"], spec["min_observations"])
    with engine.begin() as conn:
        conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job["id"])
                     .values(universe_json=_dumps({**info, "stock_ids": ids})))
    return ids


def _mark(job_id: str, run_index: int, **values) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.optimization_runs).where(
            (t.optimization_runs.c.job_id == job_id) & (t.optimization_runs.c.run_index == run_index))
            .values(**values))


def _find_cached(run_hash: str, job_id: str) -> dict | None:
    with engine.connect() as conn:
        return query_one(conn, """SELECT job_id,metrics_json,diagnostics_json FROM optimization_runs
            WHERE run_hash=:h AND status='completed' AND job_id != :j
            ORDER BY finished_at DESC LIMIT 1""", h=run_hash, j=job_id)


def _finish_job(job_id: str, status: str, error: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id)
                     .values(status=status, error=error, finished_at=_now()))


def execute_job(job_id: str) -> None:
    """Run a queued (or resumed) job.  Never raises; failures are recorded.

    Individual point failures are recorded on their run row and do not stop the
    job.  Panel/universe failures fail the job (runs stay pending -> resumable).
    """
    with engine.begin() as conn:
        claimed = conn.execute(update(t.optimization_jobs).where(
            (t.optimization_jobs.c.id == job_id) & (t.optimization_jobs.c.status == "queued"))
            .values(status="running", started_at=_now(), error=None, finished_at=None)).rowcount
    if not claimed:
        return
    try:
        _execute(job_id)
    except BaseException as exc:  # noqa: BLE001 - record any failure, then re-raise interrupts
        _finish_job(job_id, "failed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"[:4000])
        if not isinstance(exc, Exception):
            raise


def _execute(job_id: str) -> None:
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
        pending = query_all(conn, """SELECT run_index,params_json FROM optimization_runs
            WHERE job_id=:j AND status='pending' ORDER BY run_index""", j=job_id)
    request = _loads(job["request_json"])
    ids = _ensure_universe(job, request)
    ids_hash = _hash(ids)
    with engine.connect() as conn:
        fingerprint = lab.data_fingerprint(conn, ids, request["panel_start"], request["research_end"])
        repro = {**lab._repro(), "opt_version": OPT_VERSION, "data_fingerprint": fingerprint}
    with engine.begin() as conn:
        conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id)
                     .values(repro_json=_dumps(repro)))

    panel = None   # built lazily, at most once per execution, then reused for every point
    for row in pending:
        if _cancel_requested(job_id):
            _finish_job(job_id, "cancelled")
            return
        idx, flat = row["run_index"], _loads(row["params_json"])
        try:
            resolved, config = _resolve_point(request, flat)
            run_hash = _run_hash(request, resolved, config, fingerprint, ids_hash)
            cached = _find_cached(run_hash, job_id)
        except Exception as exc:  # noqa: BLE001 - one bad point must not kill the job
            _mark(job_id, idx, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000],
                  finished_at=_now())
            continue
        if cached:
            _mark(job_id, idx, status="completed", run_hash=run_hash, cached_from_job=cached["job_id"],
                  metrics_json=cached["metrics_json"], diagnostics_json=cached["diagnostics_json"],
                  error=None, finished_at=_now())
            continue
        if panel is None:
            panel = build_panel(request["panel_start"], request["research_end"], ids,
                                publication_mode=request["publication_mode"],
                                benchmark_id=request["benchmark_id"], db_engine=engine)
            coverage = {**lab.panel_coverage(panel, ids), "research_start": request["research_start"],
                        "research_end": request["research_end"], "panel_start": request["panel_start"]}
            with engine.begin() as conn:
                conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id)
                             .values(coverage_json=_dumps(coverage), panel_loads=t.optimization_jobs.c.panel_loads + 1))
        _mark(job_id, idx, status="running", run_hash=run_hash, started_at=_now())
        began = time.monotonic()
        try:
            metrics, diagnostics = evaluate_point(panel, request["template_key"], resolved, config,
                                                  request["research_start"])
            diagnostics["elapsed_seconds"] = round(time.monotonic() - began, 3)
        except Exception as exc:  # noqa: BLE001
            _mark(job_id, idx, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000],
                  finished_at=_now())
            continue
        _mark(job_id, idx, status="completed", metrics_json=_dumps(metrics),
              diagnostics_json=_dumps(diagnostics), error=None, finished_at=_now())
    with engine.connect() as conn:
        n = {r["status"]: r["n"] for r in query_all(
            conn, "SELECT status, COUNT(*) n FROM optimization_runs WHERE job_id=:j GROUP BY status",
            j=job_id)}
    if not n.get("completed"):
        _finish_job(job_id, "failed", "no run completed successfully")
    else:
        _finish_job(job_id, "completed")


# ── lifecycle: cancel / resume / recover ────────────────
def cancel_job(job_id: str) -> dict:
    """Cooperative cancel.  A queued job is cancelled immediately; a running job stops
    before its next point (the point in flight finishes: the engine cannot be
    interrupted mid-run).  Completed points are kept and the job is resumable."""
    with engine.begin() as conn:
        job = _job_row(conn, job_id)
        if job["status"] in TERMINAL:
            raise RuntimeError(f"job is already {job['status']}")
        if job["status"] == "queued":
            conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id)
                         .values(status="cancelled", finished_at=_now()))
        else:
            conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id)
                         .values(cancel_requested=1))
    return get_job(job_id)


def resume_job(job_id: str, retry_failed: bool = False) -> dict:
    """Re-queue a failed/cancelled (or partially failed, with retry_failed) job.  Completed
    points are never recomputed.  Caller schedules ``execute_job``."""
    with engine.begin() as conn:
        job = _job_row(conn, job_id)
        allowed = ("failed", "cancelled") + (("completed",) if retry_failed else ())
        if job["status"] not in allowed:
            raise RuntimeError(f"job is {job['status']}; nothing to resume")
        conn.execute(update(t.optimization_runs).where(
            (t.optimization_runs.c.job_id == job_id) & (t.optimization_runs.c.status == "running"))
            .values(status="pending"))
        if retry_failed:
            conn.execute(update(t.optimization_runs).where(
                (t.optimization_runs.c.job_id == job_id) & (t.optimization_runs.c.status == "failed"))
                .values(status="pending", error=None))
        pending = query_one(conn, "SELECT COUNT(*) n FROM optimization_runs WHERE job_id=:j "
                            "AND status='pending'", j=job_id)["n"]
        if not pending:
            raise RuntimeError("no pending runs to resume")
        conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == job_id).values(
            status="queued", cancel_requested=0, retry_failed=int(retry_failed), error=None,
            finished_at=None))
    return {"job_id": job_id, "status": "queued", "queued": True, "pending_runs": pending}


def recover_interrupted() -> int:
    """Startup hook: the in-process worker died with the server.  Jobs become failed
    (resumable); their half-done point returns to pending."""
    with engine.begin() as conn:
        ids = [r["id"] for r in query_all(conn, "SELECT id FROM optimization_jobs "
                                          "WHERE status IN ('queued','running')")]
        for jid in ids:
            conn.execute(update(t.optimization_runs).where(
                (t.optimization_runs.c.job_id == jid) & (t.optimization_runs.c.status == "running"))
                .values(status="pending"))
            conn.execute(update(t.optimization_jobs).where(t.optimization_jobs.c.id == jid).values(
                status="failed", finished_at=_now(),
                error="interrupted: server restarted before the job finished (resumable)"))
    return len(ids)


# ── read side ───────────────────────────────────────────
def _counts(conn, job_id: str) -> dict:
    by = {r["status"]: r["n"] for r in query_all(
        conn, "SELECT status, COUNT(*) n FROM optimization_runs WHERE job_id=:j GROUP BY status", j=job_id)}
    cached = query_one(conn, "SELECT COUNT(*) n FROM optimization_runs WHERE job_id=:j "
                       "AND cached_from_job IS NOT NULL", j=job_id)["n"]
    return {"pending": by.get("pending", 0), "running": by.get("running", 0),
            "completed": by.get("completed", 0), "failed": by.get("failed", 0), "cached": cached}


def provenance_view(job: Mapping) -> dict:
    """Immutable provenance; NULL columns (jobs created before F0) are reported, never inferred."""
    known = job["definition_hash"] is not None
    return {
        "known": known,
        "strategy_id": job["strategy_id"], "strategy_version_id": job["strategy_version_id"],
        "strategy_version_no": job["strategy_version_no"], "definition_hash": job["definition_hash"],
        "parent_job_id": job["parent_job_id"], "root_job_id": job["root_job_id"],
        "revision_no": job["revision_no"], "revision_kind": job["revision_kind"],
        "revision_reason": job["revision_reason"], "revision": _loads(job["revision_json"]),
        "warnings": [] if known else [warning_contract.structural("LEGACY_JOB_NO_PROVENANCE")],
    }


def _job_view(conn, job: Mapping) -> dict:
    request = _loads(job["request_json"])
    universe = _loads(job["universe_json"])
    counts = _counts(conn, job["id"])
    total = job["total_runs"] or 0
    done = counts["completed"] + counts["failed"]
    search = dict(request["search"])
    req = {k: v for k, v in request.items() if k not in ("stock_ids", "search")}
    return {
        "id": job["id"], "template_key": job["template_key"], "status": job["status"],
        "objective": job["objective"], "error": job["error"],
        "cancel_requested": bool(job["cancel_requested"]), "panel_loads": job["panel_loads"],
        "progress": {"total": total, "completed": counts["completed"], "failed": counts["failed"],
                     "pending": counts["pending"], "running": counts["running"],
                     "cached": counts["cached"], "fraction": (done / total) if total else 0.0},
        "partial": counts["failed"] > 0 and counts["completed"] > 0,
        "search": search, "request": {**req, "stock_count": len(request["stock_ids"] or
                                                                (universe or {}).get("stock_ids") or [])},
        "universe": ({k: v for k, v in universe.items() if k != "stock_ids"} if universe else None),
        "coverage": _loads(job["coverage_json"]), "reproducibility": _loads(job["repro_json"]),
        "created_at": job["created_at"], "started_at": job["started_at"],
        "finished_at": job["finished_at"], "notice": NOTICE,
        "provenance": provenance_view(job),
    }


def get_job(job_id: str) -> dict:
    with engine.connect() as conn:
        return _job_view(conn, _job_row(conn, job_id))


def list_jobs(limit: int = 50, template_key: str | None = None) -> list[dict]:
    with engine.connect() as conn:
        rows = query_all(conn, "SELECT * FROM optimization_jobs "
                         + ("WHERE template_key=:k " if template_key else "")
                         + "ORDER BY created_at DESC, rowid DESC LIMIT :n",
                         n=max(1, min(limit, 200)), **({"k": template_key} if template_key else {}))
        out = []
        for r in rows:
            v = _job_view(conn, r)
            out.append({k: v[k] for k in ("id", "template_key", "status", "objective", "progress",
                                          "partial", "created_at", "finished_at")})
        return out


def _run_rows(conn, job_id: str, status: str | None = None) -> list[dict]:
    rows = query_all(conn, "SELECT * FROM optimization_runs WHERE job_id=:j "
                     + ("AND status=:s " if status else "") + "ORDER BY run_index",
                     j=job_id, **({"s": status} if status else {}))
    out = []
    for r in rows:
        diag = _loads(r["diagnostics_json"]) or {}
        out.append({
            "run_index": r["run_index"], "point_key": r["point_key"], "status": r["status"],
            "params": _loads(r["params_json"]), "metrics": _loads(r["metrics_json"]),
            "diagnostics": diag, "error": r["error"], "cached_from_job": r["cached_from_job"],
            "run_hash": r["run_hash"]})
    return out


def _flags(row: Mapping) -> list[str]:
    flags = []
    if (row["metrics"] or {}).get("unresolved_action_count"):
        flags.append("unresolved_actions")
    if ((row["diagnostics"] or {}).get("coverage") or {}).get("signals_empty"):
        flags.append("empty_signals")
    if row["cached_from_job"]:
        flags.append("cached")
    return flags


def get_results(job_id: str, objective: str | None = None, order: str = "objective",
                limit: int = 100, offset: int = 0, status: str | None = None) -> dict:
    """Runs ordered for exploration.  Every row carries risk, cost and data-quality fields,
    not just the objective."""
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
        name, key, higher = _objective(objective, job["objective"])
        rows = _run_rows(conn, job_id, status)
        view = _job_view(conn, job)
    if order not in ("objective", "run_index"):
        raise ValueError("order must be objective or run_index")
    ordered = sort_runs(rows, name) if order == "objective" else rows
    ranks = {r["run_index"]: i + 1 for i, r in enumerate(
        x for x in sort_runs(rows, name) if x["status"] == "completed"
        and _finite((x["metrics"] or {}).get(key)))}
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    page = ordered[offset:offset + limit]
    out = []
    for r in page:
        m = r["metrics"] or {}
        d = r["diagnostics"]
        out.append({
            "run_index": r["run_index"], "status": r["status"], "params": r["params"],
            "exploration_rank": ranks.get(r["run_index"]),
            "objective_value": m.get(key),
            "summary": {k: m.get(k) for k in SUMMARY_KEYS},
            "unresolved_action_count": d.get("unresolved_action_count"),
            "unresolved_action_count_in_window": d.get("unresolved_action_count_in_window"),
            "warnings_count": d.get("warnings_count"), "coverage": d.get("coverage"),
            "flags": _flags(r), "error": r["error"], "cached_from_job": r["cached_from_job"]})
    return {"job": {k: view[k] for k in ("id", "template_key", "status", "progress", "partial")},
            "objective": {"name": name, "metric": key, "higher_is_better": higher},
            "ordering": "exploration order by objective; not a recommendation",
            "in_sample": True, "research_window": [view["request"]["research_start"],
                                                    view["request"]["research_end"]],
            "notice": NOTICE, "total": len(rows), "offset": offset, "rows": out}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _analysis_inputs(job_id: str, objective: str | None):
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
        name, key, higher = _objective(objective, job["objective"])
        rows = _run_rows(conn, job_id)
        view = _job_view(conn, job)
    request = _loads(job["request_json"])
    done = [r for r in rows if r["status"] == "completed"]
    points = [{"key": r["point_key"], "params": r["params"], "run_index": r["run_index"],
               "value": (r["metrics"] or {}).get(key), "metrics": r["metrics"] or {}} for r in done]
    space = request["search"]["space"] if request["search"]["mode"] == "grid" else None
    topo = search_space.topology(job["template_key"], space, [p["params"] for p in points])
    return job, view, request, rows, done, points, topo, (name, key, higher)


def severity_signals(request: Mapping, done: list[Mapping], failed: int, analysis: Mapping) -> dict:
    """Stable warning severity (info/caution/critical) for one job; no scoring, no UI logic."""
    items = [warning_contract.structural("IN_SAMPLE_ONLY", "results are in-sample over the research window")]
    if request["search"]["sampled"]:
        items.append(warning_contract.structural("SAMPLED_GRID"))
    plateau = analysis["plateau"]
    label = plateau["label"]
    if label == "insufficient_data":
        items.append(warning_contract.structural("INSUFFICIENT_ROBUSTNESS_DATA"))
    elif label == "isolated_peak":
        items.append(warning_contract.structural("ISOLATED_PEAK"))
    elif label == "scattered":
        items.append(warning_contract.structural("SCATTERED_SURFACE"))
    if plateau.get("edge_of_search_range"):
        items.append(warning_contract.structural(
            "EDGE_OF_SEARCH_RANGE", "top point on search boundary: " + ",".join(plateau["edge_of_search_range"])))
    if failed:
        items.append(warning_contract.structural("FAILED_RUNS", f"{failed} run(s) failed"))
    diags = [(r.get("diagnostics") or {}) for r in done]
    cov = [d.get("coverage") or {} for d in diags]
    if any((c.get("signals_empty") or 0) > 0 for c in cov):
        items.append(warning_contract.structural("EMPTY_SIGNALS"))
    if cov and all((c.get("signals_in_window") or 0) > 0 and c.get("signals_empty") == c.get("signals_in_window")
                   for c in cov):
        items.append(warning_contract.structural("ALL_SIGNALS_EMPTY"))
    if any((d.get("unresolved_action_count_in_window") or 0) > 0 for d in diags):
        items.append(warning_contract.structural("UNRESOLVED_ACTIONS_IN_WINDOW"))
    items += warning_contract.classify_all(w for d in diags for w in d.get("warnings") or [])["items"]
    dedup = list({(i["code"], i["message"]): i for i in items}.values())
    return {"items": dedup, **warning_contract.summarize(dedup)}


def get_robustness(job_id: str, objective: str | None = None, top_fraction: float | None = None) -> dict:
    """Neighbourhood sensitivity / plateau classification / dispersion / diagnostics."""
    job, view, request, rows, done, points, topo, (name, key, higher) = _analysis_inputs(job_id, objective)
    rule = {}
    if top_fraction is not None:
        if not 0 < top_fraction <= 1:
            raise ValueError("top_fraction must be in (0, 1]")
        rule["top_fraction"] = top_fraction
    analysis = robustness.analyze(points, topo, higher_is_better=higher, rule=rule)
    search = request["search"]
    grid = search["grid_size"]
    return {
        "job": {k: view[k] for k in ("id", "template_key", "status", "progress", "partial")},
        "objective": {"name": name, "metric": key, "higher_is_better": higher},
        "in_sample": True, "notice": NOTICE,
        "research_window": [request["research_start"], request["research_end"]],
        "search_coverage": {
            "mode": search["mode"], "grid_size": grid, "sampled": search["sampled"],
            "runs_planned": view["progress"]["total"], "runs_completed": len(done),
            "runs_failed": sum(1 for r in rows if r["status"] == "failed"),
            "grid_fraction_evaluated": (len(done) / grid) if grid else None,
            "note": ("Sampled grid: neighbour relations are sparse; plateau labels are weaker."
                     if search["sampled"] else None)},
        "analysis": analysis,
        "diagnostics": robustness.diagnostics(
            [{"metrics": p["metrics"]} for p in points], [p["value"] for p in points], higher),
        "topology": topo,
        "severity": severity_signals(request, done, sum(1 for r in rows if r["status"] == "failed"), analysis),
        "publication_mode": request["publication_mode"],
        "job_coverage": view["coverage"], "universe": view["universe"],
    }


def get_surface(job_id: str, x: str | None = None, y: str | None = None,
                objective: str | None = None, agg: str = "median") -> dict:
    job, view, request, rows, done, points, topo, (name, key, higher) = _analysis_inputs(job_id, objective)
    flat = list(topo["axes"]) + [f"{g}.{k}" for g, c in topo["groups"].items() for k in c["keys"]]
    if x is None:
        if not flat:
            raise ValueError("job has no searchable parameters")
        x = flat[0]
        y = y if y is not None else (flat[1] if len(flat) > 1 else None)
    result = robustness.surface(points, topo, x, y, agg)
    return {"job_id": job_id, "objective": {"name": name, "metric": key, "higher_is_better": higher},
            "in_sample": True, "notice": NOTICE, **result}
