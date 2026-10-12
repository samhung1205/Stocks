"""Strategy Lab Stage F1: Walk Forward / OOS jobs (orchestration, persistence, read side).

One job = a fixed research protocol (``walkforward.protocol``) over one strategy definition and one
search space.  Folds run strictly in order and each fold does, in this order:

    training optimization (window == fold train window)
      -> freeze_from_job          (rule wf-freeze/f0.5/2: plateau medoid, else NO_STABLE_CANDIDATE)
      -> open_test_window         (BEFORE any row dated after train_end is loaded)
      -> run_oos_fold             (independent, cash start, boundary target <= train_end)
      -> persist fold result      (one transaction; a completed fold is immutable)

``NO_STABLE_CANDIDATE`` folds are never rescued: no test data is loaded, the fold is stored as
``no_candidate`` and holds cash.  After all folds a secondary *stitched* OOS ledger and the
descriptive diagnostics are produced.  Independent-fold metrics are the primary evidence; stitched
metrics are stored and labelled separately.  Everything runs in the web process (SQLite +
BackgroundTasks, no Celery/Redis) and is resumable: completed folds are never recomputed.
"""
from __future__ import annotations

import copy
import time
import traceback
import uuid
from typing import Any, Mapping

from sqlalchemy import delete, insert, update

from app.db import engine, query_all, query_one
from app.db import tables as t
from app.services import optimization_lab as opt
from app.services import strategy_lab as lab
from app.services import warning_contract
from app.services.optimization import search_space
from app.services.strategy import metrics as strategy_metrics
from app.services.strategy.panel import build_panel
from app.services.strategy.signals import TemplateSignalRule
from app.services.strategy.templates import TEMPLATES
from app.services.walkforward import diagnostics as wf_diag
from app.services.walkforward import freeze as wf_freeze
from app.services.walkforward import oos as wf_oos
from app.services.walkforward import protocol as wf
from app.services.walkforward import stitched as wf_stitched

WF_VERSION = "walkforward/f1/1"
TERMINAL = ("completed", "failed", "cancelled")
NOTICE = ("Out-of-sample evidence from at most a handful of folds. Independent-fold metrics are the "
          "primary evidence; the stitched series is secondary and not independent. All aggregates "
          "are descriptive: no significance is claimed. Not a forecast, not investment advice.")
SEARCH_KEYS = ("search_space", "combinations", "max_runs", "seed", "budget_policy")
_now, _dumps, _loads, _hash, _clean = lab._now, lab._dumps, lab._loads, lab._hash, lab._clean


class _Cancelled(Exception):
    pass


# ── creation ────────────────────────────────────────────
def _plan_folds(start: str, end: str, protocol: Mapping) -> dict:
    with engine.connect() as conn:
        sessions = [r["date"] for r in query_all(
            conn, "SELECT date FROM trading_calendar WHERE date BETWEEN :s AND :e ORDER BY date", s=start, e=end)]
        if not sessions:
            sessions = [r["date"] for r in query_all(
                conn, "SELECT DISTINCT date FROM daily_quotes WHERE date BETWEEN :s AND :e ORDER BY date",
                s=start, e=end)]
    return wf.generate_folds(start, end, sessions, protocol)


def create_job(payload: Mapping) -> dict:
    """Validate, freeze the plan (protocol, folds, search space, definition) and queue a job."""
    key = payload.get("template_key")
    if key not in TEMPLATES:
        raise ValueError(f"unknown strategy template: {key}")
    protocol = wf.validate(payload.get("protocol"))
    if protocol["gap_sessions"] != 0:
        raise ValueError("Walk Forward OOS contract requires gap_sessions == 0")
    if protocol["candidate_rule"] != wf_freeze.RULE_VERSION:
        raise ValueError(f"candidate_rule must be {wf_freeze.RULE_VERSION}")
    backtest = copy.deepcopy(dict(payload.get("backtest") or {}))
    missing = dict(backtest.get("missing") or {})
    if missing.get("execution", protocol["execution_policy"]) != protocol["execution_policy"]:
        raise ValueError(f"Walk Forward requires missing.execution={protocol['execution_policy']!r} "
                         "(protocol execution_policy)")
    backtest["missing"] = {**missing, "execution": protocol["execution_policy"]}
    params, backtest, resolved = lab.validate_definition(key, payload.get("params"), backtest)
    mode = payload.get("publication_mode", "assumed")
    if mode not in ("strict", "assumed"):
        raise ValueError("publication_mode must be strict or assumed")
    if mode == "strict" and key in opt.FUNDAMENTAL_TEMPLATES:
        raise ValueError("fundamental templates are researched in assumed mode only")
    start = opt._iso(payload.get("data_start", opt.RESEARCH_START), "data_start")
    end = opt._iso(payload.get("data_end", opt.RESEARCH_END), "data_end")
    if start >= end:
        raise ValueError("data_start must be before data_end")
    panel_start = opt._iso(payload.get("panel_start") or min(opt.PANEL_WARMUP_START, start), "panel_start")
    if panel_start > start:
        raise ValueError("panel_start must not be after data_start")
    objective, _, _ = opt._objective(payload.get("objective"))
    search_req = {k: payload[k] for k in SEARCH_KEYS if payload.get(k) is not None}
    plan = search_space.plan(key, search_req, opt._base_flat(resolved))       # validates the space
    ids = payload.get("stock_ids")
    if ids:
        if not isinstance(ids, (list, tuple)):
            raise ValueError("stock_ids must be a list")
        ids = sorted(dict.fromkeys(str(s) for s in ids))
    universe_spec = {**opt.DEFAULT_UNIVERSE, **(payload.get("universe") or {})}
    folds_plan = _plan_folds(start, end, protocol)
    if not folds_plan["folds"]:
        raise ValueError("protocol yields no folds for this data window")
    request = {
        "template_key": key, "params": params, "backtest": backtest, "objective": objective,
        "data_start": start, "data_end": end, "panel_start": panel_start, "publication_mode": mode,
        "benchmark_id": payload.get("benchmark_id", "TAIEX_TR"), "universe_spec": universe_spec,
        "stock_ids": ids or None, "search_request": search_req,
        "search": {k: plan[k] for k in ("mode", "space", "grid_size", "skipped_invalid", "sampled",
                                        "max_runs", "seed")},
        "risk_free_rate": lab.build_backtest_config(backtest, key, params).risk_free_rate}
    with engine.begin() as conn:
        prov = opt._link_strategy(conn, payload, key, params, backtest, resolved)
    provenance = {"wf_version": WF_VERSION, "oos_contract": wf_oos.CONTRACT_VERSION,
                  "stitched_contract": wf_stitched.CONTRACT_VERSION, "opt_version": opt.OPT_VERSION,
                  "engine_version": lab.ENGINE_VERSION, "freeze_rule": wf_freeze.RULE_VERSION,
                  "universe_spec": universe_spec, "universe_kind": "explicit" if ids else "monthly_union",
                  "warning_contract": warning_contract.CONTRACT_VERSION}
    job_hash = _hash({"wf": WF_VERSION, "request": {k: v for k, v in request.items() if k != "objective"},
                      "objective": objective, "protocol": protocol, "strategy_version_id": prov["strategy_version_id"]})
    folds = folds_plan["folds"]
    with engine.begin() as conn:
        hit = query_one(conn, "SELECT id,status FROM walkforward_jobs WHERE job_hash=:h AND "
                        "status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", h=job_hash)
        if hit:
            return {"job_id": hit["id"], "status": hit["status"], "queued": False,
                    "duplicate_of_active_job": True, "total_folds": len(folds)}
        job_id = uuid.uuid4().hex
        conn.execute(insert(t.walkforward_jobs).values(
            id=job_id, template_key=key, status="queued", job_hash=job_hash,
            request_json=_dumps(request), protocol_json=_dumps(protocol),
            protocol_version=wf.PROTOCOL_VERSION,
            folds_json=_dumps({"folds": folds, "dropped": folds_plan["dropped"], "notes": folds_plan["notes"]}),
            total_folds=len(folds), strategy_id=prov["strategy_id"],
            strategy_version_id=prov["strategy_version_id"], strategy_version_no=prov["strategy_version_no"],
            definition_hash=prov["definition_hash"], candidate_rule=protocol["candidate_rule"],
            execution_policy=protocol["execution_policy"], publication_mode=mode,
            provenance_json=_dumps(provenance), cancel_requested=0, created_at=_now()))
        conn.execute(insert(t.walkforward_folds), [
            {"job_id": job_id, "fold_index": f["index"], "status": "pending", "train_start": f["train_start"],
             "train_end": f["train_end"], "test_start": f["test_start"], "test_end": f["test_end"],
             "truncated": int(f["truncated"])} for f in folds])
    return {"job_id": job_id, "status": "queued", "queued": True, "total_folds": len(folds),
            "notes": folds_plan["notes"], "dropped": folds_plan["dropped"]}


# ── helpers ─────────────────────────────────────────────
def _job_row(conn, job_id: str) -> dict:
    row = query_one(conn, "SELECT * FROM walkforward_jobs WHERE id=:i", i=job_id)
    if not row:
        raise LookupError(f"walk-forward job {job_id} not found")
    return row


def _fold_rows(conn, job_id: str) -> list[dict]:
    return query_all(conn, "SELECT * FROM walkforward_folds WHERE job_id=:j ORDER BY fold_index", j=job_id)


def _cancel_requested(job_id: str) -> bool:
    with engine.connect() as conn:
        return bool(query_one(conn, "SELECT cancel_requested c FROM walkforward_jobs WHERE id=:i", i=job_id)["c"])


def _fold_update(job_id: str, idx: int, **values) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.walkforward_folds).where(
            (t.walkforward_folds.c.job_id == job_id) & (t.walkforward_folds.c.fold_index == idx)).values(**values))


def _job_update(job_id: str, **values) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.walkforward_jobs).where(t.walkforward_jobs.c.id == job_id).values(**values))


def _fold_window(row: Mapping) -> dict:
    return {"index": row["fold_index"], "train_start": row["train_start"], "train_end": row["train_end"],
            "test_start": row["test_start"], "test_end": row["test_end"], "gap_sessions": 0,
            "truncated": bool(row["truncated"])}


def _training_payload(req: Mapping, prov: Mapping, window: Mapping) -> dict:
    """The training job's research window is EXACTLY the fold's train window."""
    return {"template_key": req["template_key"], "params": req["params"], "backtest": req["backtest"],
            "objective": req["objective"], "research_start": window["train_start"],
            "research_end": window["train_end"], "panel_start": req["panel_start"],
            "publication_mode": req["publication_mode"], "benchmark_id": req["benchmark_id"],
            "universe": req["universe_spec"], "stock_ids": req["stock_ids"], **req["search_request"],
            **{k: prov[k] for k in ("strategy_id", "strategy_version_id") if prov.get(k) is not None}}


def _ensure_training(job_id: str, idx: int, train_job: str) -> None:
    status = opt.get_job(train_job)["status"]
    if status in ("failed", "cancelled"):
        try:
            opt.resume_job(train_job)
        except RuntimeError:
            pass
        status = opt.get_job(train_job)["status"]
    if status == "queued":
        opt.execute_job(train_job)
        status = opt.get_job(train_job)["status"]
    if status == "cancelled":
        raise _Cancelled()
    if status != "completed":
        err = opt.get_job(train_job)["error"]
        raise RuntimeError(f"training job {train_job} ended {status}: {err}")


def _cash_metrics() -> dict:
    return {"total_return": 0.0, "net_return": 0.0, "gross_return": 0.0, "cagr": 0.0,
            "max_drawdown": 0.0, "turnover": 0.0, "cost_drag_pct": 0.0, "sharpe": None, "sortino": None,
            "annualized_volatility": 0.0, "cash_fold": True}


def _sessions_between(conn, start: str, end: str) -> list[str]:
    rows = query_all(conn, "SELECT date FROM trading_calendar WHERE date BETWEEN :s AND :e ORDER BY date",
                     s=start, e=end)
    return [r["date"] for r in rows]


def _severity_block(items: list[dict], training: Mapping | None) -> dict:
    dedup = list({(i["code"], i["message"]): i for i in items}.values())
    block = {"items": dedup, **warning_contract.summarize(dedup)}
    if training:
        block["training"] = {"max_severity": training.get("max_severity"), "counts": training.get("counts"),
                             "codes": training.get("codes")}
    return block


def _oos_universe(req: Mapping, window: Mapping) -> tuple[list[str], dict]:
    if req["stock_ids"]:
        return list(req["stock_ids"]), {"kind": "explicit", "union_size": len(req["stock_ids"])}
    spec = req["universe_spec"]
    with engine.connect() as conn:
        ids, info = opt.monthly_universe_union(conn, window["train_start"], window["test_end"],
                                               spec["min_avg_amount"], spec["min_observations"])
    return ids, info


def _candidate(train_request: Mapping, primary_params: Mapping):
    resolved, config = opt._resolve_point(train_request, primary_params)
    return TemplateSignalRule(train_request["template_key"], resolved, None), config


def _rows_to_persist(job_id: str, series: str, fold_index: int, curve, fills, closed) -> tuple[list, list]:
    eq = [{"job_id": job_id, "series": series, "fold_index": fold_index, "date": p["date"],
           "net_equity": p["net_equity"], "gross_equity": p["gross_equity"], "benchmark": p.get("benchmark")}
          for p in curve]
    tr = [{"job_id": job_id, "series": series, "fold_index": fold_index, "kind": "fill", "seq": i,
           "date": f["date"], "stock_id": f["stock_id"], "payload_json": _dumps(f)} for i, f in enumerate(fills)]
    tr += [{"job_id": job_id, "series": series, "fold_index": fold_index, "kind": "closed", "seq": i,
            "date": c["exit_date"], "stock_id": c["stock_id"], "payload_json": _dumps(c)}
           for i, c in enumerate(closed)]
    return eq, tr


def _insert_series(conn, eq: list, tr: list) -> None:
    if eq:
        conn.execute(insert(t.walkforward_equity), eq)
    if tr:
        conn.execute(insert(t.walkforward_trades), tr)


# ── one fold ────────────────────────────────────────────
def _run_fold(job_id: str, req: Mapping, prov: Mapping, row: Mapping) -> None:
    idx, window = row["fold_index"], _fold_window(row)
    _fold_update(job_id, idx, status="running", started_at=_now(), error=None, finished_at=None)

    # 1. training optimization on exactly the train window
    train_job = row["train_job_id"]
    if not train_job:
        q = opt.create_job(_training_payload(req, prov, window))
        train_job = q["job_id"]
        _fold_update(job_id, idx, train_job_id=train_job, stage="training")
    _ensure_training(job_id, idx, train_job)
    if _cancel_requested(job_id):
        raise _Cancelled()
    job_t, view_t, request_t, rows_t, done_t, points_t, topo, _ = opt._analysis_inputs(train_job, None)
    if (request_t["research_start"], request_t["research_end"]) != (window["train_start"], window["train_end"]):
        raise RuntimeError("TRAIN_WINDOW_MISMATCH: training job window differs from the fold train window")
    robust = opt.get_robustness(train_job)
    training_sev = robust["severity"]

    # 2. freeze (before any test data is touched)
    try:
        fz = wf_freeze.freeze_from_job(train_job, window)
    except ValueError as exc:
        if str(exc).startswith("NO_STABLE_CANDIDATE"):
            cash = lab.build_backtest_config(req["backtest"], req["template_key"], req["params"]).initial_cash
            return _complete_no_candidate(job_id, idx, window, str(exc), robust, training_sev, cash)
        raise
    _fold_update(job_id, idx, freeze_id=fz["id"], candidate_digest=fz["candidate_digest"], stage="frozen")

    # 3. open the test window, THEN load test-window data
    opened = wf_freeze.open_test_window(fz["id"])
    _fold_update(job_id, idx, stage="test_opened", test_opened_at=opened["test_opened_at"])
    primary = fz["primary_params"]
    rule, config = _candidate(request_t, primary)
    ids, uinfo = _oos_universe(req, window)
    panel = build_panel(req["panel_start"], window["test_end"], ids, publication_mode=req["publication_mode"],
                        benchmark_id=req["benchmark_id"], db_engine=engine)
    with engine.connect() as conn:
        fingerprint = lab.data_fingerprint(conn, ids, req["panel_start"], window["test_end"])
    began = time.monotonic()
    out = wf_oos.run_oos_fold(panel, rule, config, window)
    result, metrics = out["result"], out["metrics"]
    metrics = {**metrics, "cost_drag_pct": (metrics["gross_return"] - metrics["net_return"])
               if metrics.get("gross_return") is not None and metrics.get("net_return") is not None else None}
    meta = result["metadata"]
    curve = [p for p in result["equity_curve"] if window["test_start"] <= p["date"] <= window["test_end"]]
    fills = [f for f in result["fills"] if f["date"] <= window["test_end"]]
    closed = [c for c in result["closed_trades"] if c["exit_date"] <= window["test_end"]]

    # 4. diagnostics + severity
    edge = wf_diag.primary_edge_axes(primary, topo) + wf_diag.group_edge_axes(primary, points_t, topo)
    items = list(fz["warnings"])
    if edge:
        items.append(warning_contract.structural("PRIMARY_ON_SEARCH_EDGE", "frozen primary at search boundary: " + ",".join(edge)))
    if window["truncated"]:
        items.append(warning_contract.structural("PARTIAL_FOLD_TEST_WINDOW"))
    if meta["partial_rebalance_count"]:
        items.append(warning_contract.structural("PARTIAL_REBALANCE_USED", f"{meta['partial_rebalance_count']} partial rebalance(s)"))
    if meta["unresolved_action_count"]:
        items.append(warning_contract.structural("UNRESOLVED_ACTIONS_IN_WINDOW", f"{meta['unresolved_action_count']} unresolved action(s)"))
    if not fills:
        items.append(warning_contract.structural("EMPTY_SIGNALS", "frozen candidate made no trades in the test window"))
    items += warning_contract.classify_all(result["warnings"])["items"]
    sig = out["boundary_signal"]
    end_point = curve[-1]
    is_row = next((r for r in rows_t if r["point_key"] == fz["primary_point_key"]), None)
    is_summary = {"metrics": {k: (is_row["metrics"] or {}).get(k) for k in opt.SUMMARY_KEYS} if is_row else None,
                  "train_window": [window["train_start"], window["train_end"]], "train_job_id": train_job,
                  "plateau_label": fz["plateau_label"], "is_peak_key": robust["analysis"]["plateau"].get("peak_key"),
                  "region_size": len(fz["region"]), "in_sample": True,
                  "objective": view_t["objective"], "search_grid_size": request_t["search"]["grid_size"],
                  "runs_completed": len(done_t)}
    oos_info = {
        "contract": out["contract"], "mode": out["mode"], "initial_state": out["initial_state"],
        "boundary_date": out["boundary_date"],
        "boundary_candidates": len(getattr(sig, "candidates", ()) or ()) if sig is not None else None,
        "effective_first_fill_date": out["first_fill_date"], "fills_before_test_start": out["fills_before_test_start"],
        "n_fills": len(fills), "n_closed_trades": len(closed),
        "unresolved_action_count": meta["unresolved_action_count"],
        "unresolved_actions_sample": meta["unresolved_actions"][:10],
        "partial_rebalance_count": meta["partial_rebalance_count"],
        "partial_rebalances_sample": meta["partial_rebalances"][:10],
        "warnings_count": len(result["warnings"]), "warnings": result["warnings"][:100],
        "publication_mode": meta["publication_mode"], "test_sessions": len(curve),
        "terminal": {"open_positions": len(result["open_positions"]), "ending_cash": result["ending_cash"],
                     "net_equity": end_point["net_equity"]},
        "elapsed_seconds": round(time.monotonic() - began, 3)}
    oos_info["boundary_signal_present"] = sig is not None
    eq, tr = _rows_to_persist(job_id, "fold", idx, curve, fills, closed)
    with engine.begin() as conn:                   # one transaction: evidence lands whole or not at all
        _insert_series(conn, eq, tr)
        conn.execute(update(t.walkforward_folds).where(
            (t.walkforward_folds.c.job_id == job_id) & (t.walkforward_folds.c.fold_index == idx)).values(
            status="completed", stage="oos_done", outcome="evaluated", plateau_label=fz["plateau_label"],
            primary_point_key=fz["primary_point_key"], primary_params_json=_dumps(primary),
            region_json=_dumps(fz["region"]), edge_axes_json=_dumps(fz["edge_axes"]),
            primary_edge_axes_json=_dumps(edge), freeze_json=_dumps({k: fz[k] for k in (
                "id", "rule_version", "candidate_digest", "created_at", "warnings",
                "job_id", "root_job_id", "definition_hash")}),
            is_json=_dumps(is_summary), oos_metrics_json=_dumps(_clean(metrics)),
            oos_json=_dumps(oos_info), severity_json=_dumps(_severity_block(items, training_sev)),
            universe_json=_dumps({**uinfo, "stock_ids": ids}), oos_repro_json=_dumps(
                {**lab._repro(), "data_fingerprint": fingerprint}), finished_at=_now(), error=None))


def _complete_no_candidate(job_id: str, idx: int, window: Mapping, message: str, robust: Mapping,
                           training_sev: Mapping, cash: float) -> None:
    """No stable candidate: hold cash, load no test data, keep the reason verbatim."""
    head, _, tail = message.partition(": ")          # "NO_STABLE_CANDIDATE: LABEL:x, ..." (freeze contract text)
    reasons = [head] + ([r.strip() for r in tail.split(", ")] if tail else [])
    items = [warning_contract.structural("NO_STABLE_CANDIDATE", message),
             warning_contract.structural("FOLD_HELD_CASH", "test window held in cash; no parameters were traded")]
    if window["truncated"]:
        items.append(warning_contract.structural("PARTIAL_FOLD_TEST_WINDOW"))
    with engine.connect() as conn:
        sessions = _sessions_between(conn, window["test_start"], window["test_end"])
    curve = [{"date": d, "net_equity": cash, "gross_equity": cash, "benchmark": None} for d in sessions]
    plateau = robust["analysis"]["plateau"]
    eq, _ = _rows_to_persist(job_id, "fold", idx, curve, [], [])
    with engine.begin() as conn:
        _insert_series(conn, eq, [])
        conn.execute(update(t.walkforward_folds).where(
            (t.walkforward_folds.c.job_id == job_id) & (t.walkforward_folds.c.fold_index == idx)).values(
            status="completed", stage="oos_done", outcome="no_candidate", plateau_label=plateau["label"],
            edge_axes_json=_dumps(plateau.get("edge_of_search_range") or []),
            freeze_json=_dumps({"no_candidate": True, "message": message, "reasons": reasons,
                                "plateau": {k: plateau.get(k) for k in (
                                    "label", "reasons", "peak_key", "region_size", "peak_drop")}}),
            oos_metrics_json=_dumps(_cash_metrics()),
            oos_json=_dumps({"mode": "cash", "initial_state": "cash", "effective_first_fill_date": None,
                             "fills_before_test_start": 0, "n_fills": 0, "n_closed_trades": 0,
                             "unresolved_action_count": 0, "partial_rebalance_count": 0,
                             "test_sessions": len(curve), "test_data_loaded": False,
                             "terminal": {"open_positions": 0, "ending_cash": cash, "net_equity": cash}}),
            severity_json=_dumps(_severity_block(items, training_sev)), finished_at=_now(), error=None))


# ── job execution ───────────────────────────────────────
def execute_job(job_id: str) -> None:
    """Run a queued (or resumed) job.  Never raises; failures are recorded and resumable."""
    with engine.begin() as conn:
        claimed = conn.execute(update(t.walkforward_jobs).where(
            (t.walkforward_jobs.c.id == job_id) & (t.walkforward_jobs.c.status == "queued"))
            .values(status="running", started_at=_now(), error=None, finished_at=None)).rowcount
    if not claimed:
        return
    try:
        _execute(job_id)
    except _Cancelled:
        _job_update(job_id, status="cancelled", finished_at=_now())
    except BaseException as exc:  # noqa: BLE001
        _job_update(job_id, status="failed", finished_at=_now(),
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"[:4000])
        if not isinstance(exc, Exception):
            raise


def _execute(job_id: str) -> None:
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
    req = _loads(job["request_json"])
    prov = {k: job[k] for k in ("strategy_id", "strategy_version_id")}
    _job_update(job_id, repro_json=_dumps({**lab._repro(), "wf_version": WF_VERSION}))
    while True:
        if _cancel_requested(job_id):
            raise _Cancelled()
        with engine.connect() as conn:
            row = next((r for r in _fold_rows(conn, job_id) if r["status"] != "completed"), None)
        if row is None:
            break
        try:
            _run_fold(job_id, req, prov, row)
        except _Cancelled:
            _fold_update(job_id, row["fold_index"], status="pending")
            raise
        except Exception as exc:  # noqa: BLE001 - record on the fold, stop the job, stay resumable
            _fold_update(job_id, row["fold_index"], status="failed",
                         error=f"{type(exc).__name__}: {exc}"[:2000], finished_at=_now())
            raise
    _finalize(job_id, job, req)


def _finalize(job_id: str, job: Mapping, req: Mapping) -> None:
    """All folds completed: secondary stitched ledger, then descriptive diagnostics."""
    stitched_meta = _build_stitched(job_id, req)
    summary = _diagnostics(job_id, stitched_meta)
    _job_update(job_id, status="completed", finished_at=_now(), stitched_status=stitched_meta["status"],
                stitched_json=_dumps(stitched_meta), summary_json=_dumps(summary), error=None)


def _build_stitched(job_id: str, req: Mapping) -> dict:
    with engine.connect() as conn:
        rows = _fold_rows(conn, job_id)
    folds = [_fold_window(r) for r in rows]
    label = {"evidence": "stitched_secondary", "independent": False, "contract": wf_stitched.CONTRACT_VERSION}
    items = [warning_contract.structural("STITCHED_NOT_INDEPENDENT_EVIDENCE",
                                         "one continuous ledger with inherited positions")]
    evaluated = [r for r in rows if r["outcome"] == "evaluated"]
    try:
        base_params, base_backtest, base_resolved = lab.validate_definition(
            req["template_key"], req["params"], req["backtest"])
        base_config = lab.build_backtest_config(base_backtest, req["template_key"], base_params)
        rules_by, cfg_by, uni_by = {}, {}, {}
        for r in evaluated:
            train_req = _loads(query_one_train(r["train_job_id"])["request_json"])
            rules_by[r["fold_index"]], cfg_by[r["fold_index"]] = _candidate(
                train_req, _loads(r["primary_params_json"]))
            u = _loads(r["universe_json"]) or {}
            uni_by[r["fold_index"]] = None if u.get("kind") == "explicit" else u["stock_ids"]
        if evaluated:
            # panel = superset of the per-fold point-in-time universes (they only ever grow);
            # each fold's rule sees only its own U_k (see stitched.UniverseView)
            ids = sorted({sid for r in evaluated for sid in _loads(r["universe_json"])["stock_ids"]})
            uinfo = {"kind": "per_fold_point_in_time", "fold_universe_sizes": {
                r["fold_index"]: len(_loads(r["universe_json"])["stock_ids"]) for r in evaluated}}
            panel = build_panel(req["panel_start"], folds[-1]["test_end"], ids,
                                publication_mode=req["publication_mode"], benchmark_id=req["benchmark_id"],
                                db_engine=engine)
            out = wf_stitched.run_stitched(panel, folds, rules_by, cfg_by, base_config, uni_by)
            result = out["result"]
        else:                                        # every fold held cash: nothing to trade, no price data needed
            ids, uinfo, result = [], {"kind": "none"}, None
    except (ValueError, AssertionError) as exc:
        if isinstance(exc, AssertionError):
            raise
        return {**label, "status": "unavailable", "reason": str(exc), "warnings": items}
    start, end = folds[0]["test_start"], folds[-1]["test_end"]
    if result is None:
        with engine.connect() as conn:
            sessions = _sessions_between(conn, start, end)
        cash = base_config.initial_cash
        curve = [{"date": d, "net_equity": cash, "gross_equity": cash, "benchmark": None} for d in sessions]
        fills, closed, metrics, warnings_, meta = [], [], _cash_metrics(), [], {
            "unresolved_actions": [], "unresolved_action_count": 0, "partial_rebalance_count": 0,
            "partial_rebalances": [], "publication_mode": req["publication_mode"]}
        ending_cash, open_positions = cash, {}
    else:
        curve = [p for p in result["equity_curve"] if start <= p["date"] <= end]
        fills = [f for f in result["fills"] if f["date"] <= end]
        closed = [c for c in result["closed_trades"] if c["exit_date"] <= end]
        trimmed = {**result, "equity_curve": [p for p in result["equity_curve"] if p["date"] <= end],
                   "fills": fills, "closed_trades": closed}
        metrics = {k: v for k, v in opt.windowed_metrics(trimmed, start, base_config.risk_free_rate).items()
                   if not k.startswith("_")}
        metrics["cost_drag_pct"] = (metrics["gross_return"] - metrics["net_return"]) \
            if metrics.get("gross_return") is not None and metrics.get("net_return") is not None else None
        warnings_, meta = result["warnings"], result["metadata"]
        ending_cash, open_positions = result["ending_cash"], result["open_positions"]
    by_date = {p["date"]: p for p in curve}
    segments, prev_end_equity = [], None
    shares: dict[str, float] = {}
    for f in folds:
        seg_end = by_date.get(f["test_end"])
        seg_start_eq = prev_end_equity if prev_end_equity is not None else (curve[0]["net_equity"] if curve else None)
        inherited = {s: round(q, 6) for s, q in shares.items() if q > 1e-9}
        for fl in fills:
            if f["test_start"] <= fl["date"] <= f["test_end"]:
                shares[fl["stock_id"]] = shares.get(fl["stock_id"], 0.0) + (
                    fl["shares"] if fl["side"] == "buy" else -fl["shares"])
        prev_end_equity = seg_end["net_equity"] if seg_end else prev_end_equity
        row = rows[f["index"]]
        segments.append({"fold": f["index"], "window": [f["test_start"], f["test_end"]], "outcome": row["outcome"],
                         "net_return": (seg_end["net_equity"] / seg_start_eq - 1) if seg_end and seg_start_eq else None,
                         "state_source": "cash" if f["index"] == 0 else "prior_oos_terminal",
                         "inherited_position_count": len(inherited),
                         "inherited_from_fold": None if f["index"] == 0 else f["index"] - 1,
                         "fills": sum(1 for fl in fills if f["test_start"] <= fl["date"] <= f["test_end"])})
    if any(r["outcome"] == "no_candidate" for r in rows) and result is not None:
        items.append(warning_contract.structural("STITCHED_LIQUIDATION_AT_NO_CANDIDATE",
                                                 "positions liquidated at the boundary of a no_candidate fold"))
    items += warning_contract.classify_all(warnings_)["items"]
    if meta["unresolved_action_count"]:
        items.append(warning_contract.structural("UNRESOLVED_ACTIONS_IN_WINDOW", f"{meta['unresolved_action_count']} unresolved action(s)"))
    if meta["partial_rebalance_count"]:
        items.append(warning_contract.structural("PARTIAL_REBALANCE_USED"))
    eq, tr = _rows_to_persist(job_id, "stitched", -1, curve, fills, closed)
    with engine.begin() as conn:
        conn.execute(delete(t.walkforward_equity).where(
            (t.walkforward_equity.c.job_id == job_id) & (t.walkforward_equity.c.series == "stitched")))
        conn.execute(delete(t.walkforward_trades).where(
            (t.walkforward_trades.c.job_id == job_id) & (t.walkforward_trades.c.series == "stitched")))
        _insert_series(conn, eq, tr)
    dedup = list({(i["code"], i["message"]): i for i in items}.values())
    return {**label, "status": "available", "window": [start, end], "metrics": _clean(metrics),
            "segments": segments, "initial_state": "cash", "state_source_policy": "prior_oos_terminal only",
            "no_candidate_policy": "liquidate to cash at the fold boundary, hold cash",
            "training_replay_used": False, "fills_before_first_test_start": sum(1 for f in fills if f["date"] < start),
            "first_fill_date": fills[0]["date"] if fills else None, "n_fills": len(fills),
            "n_closed_trades": len(closed), "unresolved_action_count": meta["unresolved_action_count"],
            "partial_rebalance_count": meta["partial_rebalance_count"],
            "terminal": {"open_positions": len(open_positions), "ending_cash": ending_cash,
                         "net_equity": curve[-1]["net_equity"] if curve else None},
            "warnings_count": len(warnings_), "warnings": warnings_[:100], "universe": {
                k: v for k, v in uinfo.items() if k != "months"} | {"stock_count": len(ids)},
            "severity": {"items": dedup, **warning_contract.summarize(dedup)},
            "note": "Secondary evidence. Independent-fold metrics are the primary OOS evidence."}


def query_one_train(train_job_id: str) -> dict:
    with engine.connect() as conn:
        return query_one(conn, "SELECT request_json FROM optimization_jobs WHERE id=:i", i=train_job_id)


def _fold_summary(row: Mapping) -> dict:
    """Compact fold record used by diagnostics and the result view."""
    sev = _loads(row["severity_json"]) or {}
    is_j, oos_j = _loads(row["is_json"]) or {}, _loads(row["oos_json"]) or {}
    region = _loads(row["region_json"]) or []
    return {
        "index": row["fold_index"], "status": row["status"], "outcome": row["outcome"],
        "truncated": bool(row["truncated"]), "plateau_label": row["plateau_label"],
        "edge_axes": _loads(row["edge_axes_json"]) or [],
        "primary_edge_axes": _loads(row["primary_edge_axes_json"]) or [],
        "primary_params": _loads(row["primary_params_json"]), "primary_key": row["primary_point_key"],
        "region_keys": [r["key"] for r in region], "candidate_digest": row["candidate_digest"],
        "is_metrics": is_j.get("metrics"), "oos_metrics": _loads(row["oos_metrics_json"]),
        "unresolved_action_count": oos_j.get("unresolved_action_count"),
        "partial_rebalance_count": oos_j.get("partial_rebalance_count"),
        "max_severity": sev.get("max_severity"), "severity_counts": sev.get("counts"),
        "training_max_severity": (sev.get("training") or {}).get("max_severity"), "error": row["error"]}


def _diagnostics(job_id: str, stitched_meta: Mapping) -> dict:
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
        rows = _fold_rows(conn, job_id)
    plan = _loads(job["folds_json"])
    diag = wf_diag.analyze([_fold_summary(r) for r in rows],
                           {"planned": job["total_folds"], "dropped": plan["dropped"], "notes": plan["notes"]})
    diag["stitched_reference"] = {
        "evidence": "stitched_secondary", "status": stitched_meta.get("status"),
        "metrics": stitched_meta.get("metrics"),
        "note": "kept apart from every independent-fold aggregate above"}
    return diag


# ── lifecycle ───────────────────────────────────────────
def cancel_job(job_id: str) -> dict:
    """Cooperative cancel: stops before the next stage; completed folds are untouched."""
    with engine.begin() as conn:
        job = _job_row(conn, job_id)
        if job["status"] in TERMINAL:
            raise RuntimeError(f"job is already {job['status']}")
        if job["status"] == "queued":
            conn.execute(update(t.walkforward_jobs).where(t.walkforward_jobs.c.id == job_id)
                         .values(status="cancelled", finished_at=_now()))
        else:
            conn.execute(update(t.walkforward_jobs).where(t.walkforward_jobs.c.id == job_id)
                         .values(cancel_requested=1))
            running = query_one(conn, "SELECT train_job_id FROM walkforward_folds WHERE job_id=:j "
                                "AND status='running' AND train_job_id IS NOT NULL", j=job_id)
    if job["status"] == "running" and running:
        try:
            opt.cancel_job(running["train_job_id"])       # best effort: in-flight point finishes
        except (RuntimeError, LookupError):
            pass
    return get_job(job_id)


def resume_job(job_id: str) -> dict:
    """Re-queue a failed/cancelled job.  Completed folds are never recomputed; failed folds return
    to pending and reuse their training job / freeze (a test window that was opened stays opened)."""
    with engine.begin() as conn:
        job = _job_row(conn, job_id)
        if job["status"] not in ("failed", "cancelled"):
            raise RuntimeError(f"job is {job['status']}; nothing to resume")
        conn.execute(update(t.walkforward_folds).where(
            (t.walkforward_folds.c.job_id == job_id) & (t.walkforward_folds.c.status.in_(("running", "failed"))))
            .values(status="pending", error=None))
        conn.execute(update(t.walkforward_jobs).where(t.walkforward_jobs.c.id == job_id).values(
            status="queued", cancel_requested=0, error=None, finished_at=None))
        pending = query_one(conn, "SELECT COUNT(*) n FROM walkforward_folds WHERE job_id=:j "
                            "AND status!='completed'", j=job_id)["n"]
    return {"job_id": job_id, "status": "queued", "queued": True, "pending_folds": pending}


def recover_interrupted() -> int:
    """Startup hook: the worker died with the server.  Active jobs become failed (resumable); a
    half-done fold returns to pending — completed folds are immutable and stay as they are."""
    with engine.begin() as conn:
        ids = [r["id"] for r in query_all(conn, "SELECT id FROM walkforward_jobs WHERE status IN ('queued','running')")]
        for jid in ids:
            conn.execute(update(t.walkforward_folds).where(
                (t.walkforward_folds.c.job_id == jid) & (t.walkforward_folds.c.status == "running"))
                .values(status="pending"))
            conn.execute(update(t.walkforward_jobs).where(t.walkforward_jobs.c.id == jid).values(
                status="failed", finished_at=_now(),
                error="interrupted: server restarted before the job finished (resumable)"))
    return len(ids)


# ── read side ───────────────────────────────────────────
def _provenance(job: Mapping, conn) -> dict:
    prov = _loads(job["provenance_json"]) or {}
    folds = _fold_rows(conn, job["id"])
    same = query_one(conn, "SELECT COUNT(*) n FROM walkforward_jobs WHERE id!=:i AND template_key=:k "
                     "AND (:d IS NULL OR definition_hash=:d)", i=job["id"], k=job["template_key"], d=job["definition_hash"])["n"]
    return {
        "strategy_id": job["strategy_id"], "strategy_version_id": job["strategy_version_id"],
        "strategy_version_no": job["strategy_version_no"], "definition_hash": job["definition_hash"],
        "protocol_version": job["protocol_version"], "candidate_rule": job["candidate_rule"],
        "execution_policy": job["execution_policy"], "publication_mode": job["publication_mode"],
        "job_hash": job["job_hash"], **prov,
        "fold_links": [{"fold": f["fold_index"], "train_job_id": f["train_job_id"], "freeze_id": f["freeze_id"],
                        "candidate_digest": f["candidate_digest"], "test_opened_at": f["test_opened_at"]}
                       for f in folds],
        "trial_disclosure": {"other_walkforward_jobs_same_template_definition": same,
                             "note": "each Walk Forward job is a separate trial of this strategy definition"}}


def _job_view(conn, job: Mapping) -> dict:
    req = _loads(job["request_json"])
    folds = _fold_rows(conn, job["id"])
    by = {}
    for f in folds:
        by[f["status"]] = by.get(f["status"], 0) + 1
    done = by.get("completed", 0)
    total = job["total_folds"]
    return {
        "id": job["id"], "template_key": job["template_key"], "status": job["status"], "error": job["error"],
        "cancel_requested": bool(job["cancel_requested"]),
        "progress": {"total": total, "completed": done, "failed": by.get("failed", 0),
                     "running": by.get("running", 0), "pending": by.get("pending", 0),
                     "evaluated": sum(1 for f in folds if f["outcome"] == "evaluated"),
                     "no_candidate": sum(1 for f in folds if f["outcome"] == "no_candidate"),
                     "fraction": done / total if total else 0.0,
                     "current_fold": next((f["fold_index"] for f in folds if f["status"] == "running"), None),
                     "current_stage": next((f["stage"] for f in folds if f["status"] == "running"), None)},
        "protocol": _loads(job["protocol_json"]),
        "request": {k: v for k, v in req.items() if k != "stock_ids"} | {"stock_count": len(req["stock_ids"] or [])},
        "stitched_status": job["stitched_status"], "provenance": _provenance(job, conn),
        "reproducibility": _loads(job["repro_json"]), "created_at": job["created_at"],
        "started_at": job["started_at"], "finished_at": job["finished_at"], "notice": NOTICE}


def get_job(job_id: str) -> dict:
    with engine.connect() as conn:
        return _job_view(conn, _job_row(conn, job_id))


def list_jobs(limit: int = 50, template_key: str | None = None) -> list[dict]:
    with engine.connect() as conn:
        rows = query_all(conn, "SELECT * FROM walkforward_jobs " + ("WHERE template_key=:k " if template_key else "")
                         + "ORDER BY created_at DESC, rowid DESC LIMIT :n", n=max(1, min(limit, 200)),
                         **({"k": template_key} if template_key else {}))
        return [{k: v[k] for k in ("id", "template_key", "status", "progress", "created_at", "finished_at")}
                for v in (_job_view(conn, r) for r in rows)]


def _fold_view(row: Mapping) -> dict:
    sev = _loads(row["severity_json"])
    return {
        "index": row["fold_index"], "status": row["status"], "stage": row["stage"], "outcome": row["outcome"],
        "no_candidate": row["outcome"] == "no_candidate",
        "windows": {"train": [row["train_start"], row["train_end"]], "test": [row["test_start"], row["test_end"]],
                    "truncated": bool(row["truncated"])},
        "train_job_id": row["train_job_id"], "freeze_id": row["freeze_id"],
        "candidate_digest": row["candidate_digest"], "plateau_label": row["plateau_label"],
        "primary": {"point_key": row["primary_point_key"], "params": _loads(row["primary_params_json"]),
                    "medoid_of_region": True} if row["primary_point_key"] else None,
        "region": _loads(row["region_json"]), "is_peak_edge_axes": _loads(row["edge_axes_json"]),
        "primary_edge_axes": _loads(row["primary_edge_axes_json"]),
        "candidate_flags": {"is_peak_on_edge": bool(_loads(row["edge_axes_json"])),
                            "primary_on_edge": bool(_loads(row["primary_edge_axes_json"]))},
        "freeze": _loads(row["freeze_json"]), "is_training": _loads(row["is_json"]),
        "evidence": "independent_fold", "oos_metrics": _loads(row["oos_metrics_json"]),
        "oos": _loads(row["oos_json"]), "severity": sev, "universe": {
            k: v for k, v in (_loads(row["universe_json"]) or {}).items() if k not in ("stock_ids", "months")} or None,
        "oos_reproducibility": _loads(row["oos_repro_json"]), "test_opened_at": row["test_opened_at"],
        "error": row["error"], "started_at": row["started_at"], "finished_at": row["finished_at"]}


def get_folds(job_id: str) -> list[dict]:
    with engine.connect() as conn:
        _job_row(conn, job_id)
        return [_fold_view(r) for r in _fold_rows(conn, job_id)]


def get_fold(job_id: str, index: int) -> dict:
    with engine.connect() as conn:
        _job_row(conn, job_id)
        row = query_one(conn, "SELECT * FROM walkforward_folds WHERE job_id=:j AND fold_index=:i", j=job_id, i=index)
    if not row:
        raise LookupError(f"fold {index} not found")
    return _fold_view(row)


def get_result(job_id: str) -> dict:
    """Job result: independent-fold evidence (primary), diagnostics, stitched (secondary, separate)."""
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
        view = _job_view(conn, job)
    return {"job": {k: view[k] for k in ("id", "template_key", "status", "progress", "provenance", "protocol")},
            "notice": NOTICE, "evidence": "independent_fold",
            "folds": [{k: f[k] for k in ("index", "status", "outcome", "no_candidate", "windows", "plateau_label",
                                          "primary", "candidate_flags", "candidate_digest", "oos_metrics", "is_training")}
                      | {"severity": (f["severity"] or {}).get("max_severity"),
                         "first_fill_date": (f["oos"] or {}).get("effective_first_fill_date")}
                      for f in get_folds(job_id)],
            "diagnostics": _loads(job["summary_json"]),
            "stitched": get_stitched(job_id, brief=True)}


def get_independent_oos(job_id: str) -> dict:
    """Independent-fold OOS results only (primary evidence) — no stitched numbers."""
    folds = get_folds(job_id)
    return {"evidence": "independent_fold", "notice": NOTICE,
            "folds": [{k: f[k] for k in ("index", "status", "outcome", "no_candidate", "windows", "oos_metrics",
                                          "oos", "severity", "candidate_flags", "candidate_digest")}
                      for f in folds]}


def get_stitched(job_id: str, brief: bool = False) -> dict:
    with engine.connect() as conn:
        job = _job_row(conn, job_id)
    if job["stitched_status"] is None:
        return {"evidence": "stitched_secondary", "available": False,
                "reason": f"job is {job['status']}; stitched OOS is built once every fold is completed"}
    data = _loads(job["stitched_json"])
    if brief:
        data = {k: v for k, v in data.items() if k not in ("warnings",)}
    return {**data, "available": data.get("status") == "available"}


def get_equity(job_id: str, series: str, fold_index: int = -1) -> dict:
    if series not in ("fold", "stitched"):
        raise ValueError("series must be fold or stitched")
    fold_index = -1 if series == "stitched" else fold_index
    with engine.connect() as conn:
        _job_row(conn, job_id)
        rows = query_all(conn, "SELECT date,net_equity,gross_equity,benchmark FROM walkforward_equity "
                         "WHERE job_id=:j AND series=:s AND fold_index=:f ORDER BY date", j=job_id, s=series, f=fold_index)
    return {"evidence": "independent_fold" if series == "fold" else "stitched_secondary",
            "fold_index": None if series == "stitched" else fold_index, "points": rows}


def get_trades(job_id: str, series: str, fold_index: int = -1, kind: str = "fill",
               limit: int = 500, offset: int = 0) -> dict:
    if series not in ("fold", "stitched") or kind not in ("fill", "closed"):
        raise ValueError("series must be fold|stitched and kind fill|closed")
    fold_index = -1 if series == "stitched" else fold_index
    limit, offset = max(1, min(limit, 2000)), max(0, offset)
    with engine.connect() as conn:
        _job_row(conn, job_id)
        total = query_one(conn, "SELECT COUNT(*) n FROM walkforward_trades WHERE job_id=:j AND series=:s "
                          "AND fold_index=:f AND kind=:k", j=job_id, s=series, f=fold_index, k=kind)["n"]
        rows = query_all(conn, "SELECT payload_json FROM walkforward_trades WHERE job_id=:j AND series=:s AND "
                         "fold_index=:f AND kind=:k ORDER BY seq LIMIT :n OFFSET :o", j=job_id, s=series, f=fold_index,
                         k=kind, n=limit, o=offset)
    return {"evidence": "independent_fold" if series == "fold" else "stitched_secondary",
            "kind": kind, "total": total, "offset": offset, "rows": [_loads(r["payload_json"]) for r in rows]}
