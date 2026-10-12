"""Stage H1: Strategy Portfolio Lab (persistence, versioning, run lifecycle).

A "strategy portfolio" is a long-lived, user-named identity (e.g. "Core Five")
combining an ARBITRARY, user-defined set of Single Strategies — never a fixed
set of template names. Everything this module does is generic over the
component list; adding a new Single Strategy never requires a code change
here (see ``app.services.portfolio.allocation`` / ``.target`` / ``.research``
for the same guarantee at the pure-function layer this module calls into).

Versioning mirrors ``strategy_lab.py``'s already-established convention:
``strategy_portfolio_versions`` rows are immutable by service-layer discipline
(never UPDATEd, only INSERTed); editing any versioned field (components,
allocation method/weights, no-candidate policy, rebalance config, constraints,
execution config) appends a new version, and a ``portfolio_research_runs`` row
always pins the exact version (and, transitively through its components, the
exact Single Strategy versions and Walk Forward jobs) it was computed from —
a later edit to the portfolio can never change what an old run shows.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import fields
from typing import Any, Mapping

from sqlalchemy import insert, update

from app.db import engine, query_all, query_one
from app.db import tables as t
from app.services import strategy_lab as lab
from app.services import walkforward_lab as wflab
from app.services.portfolio import allocation, calendar, constraints as constraints_mod
from app.services.portfolio import research as research_mod
from app.services.portfolio import series as series_mod
from app.services.portfolio.data import closes_for, corporate_actions_for, load_strategy
from app.services.strategy import metrics as strategy_metrics
from app.services.strategy.types import CostConfig

_now, _dumps, _loads, _hash = lab._now, lab._dumps, lab._loads, lab._hash

PORTFOLIO_LAB_VERSION = "portfolio-lab/h1/1"
ACTIVE_RUN_STATUSES = ("queued", "running")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
DEFAULT_INITIAL_CASH = 1_000_000.0
IMPLEMENTED_NO_CANDIDATE_POLICIES = ("cash_hold",)
_RESERVED_NO_CANDIDATE_POLICIES = ("redistribute_active",)


# ── definition validation ───────────────────────────────
def _cost_config_dict(costs: Mapping) -> dict:
    allowed = {f.name for f in fields(CostConfig)}
    unknown = set(costs) - allowed
    if unknown:
        raise ValueError(f"unknown execution.costs key: {sorted(unknown)[0]}")
    CostConfig(**costs)  # validate now (raises on negative values); stored as plain dict
    return dict(costs)


def _validate_execution(execution: Mapping | None) -> dict:
    execution = dict(execution or {})
    unknown = set(execution) - {"initial_cash", "costs"}
    if unknown:
        raise ValueError(f"unknown execution key: {sorted(unknown)[0]}")
    initial_cash = execution.get("initial_cash", DEFAULT_INITIAL_CASH)
    if isinstance(initial_cash, bool) or not isinstance(initial_cash, (int, float)) or initial_cash <= 0:
        raise ValueError("execution.initial_cash must be a positive number")
    costs = _cost_config_dict(execution.get("costs") or {})
    return {"initial_cash": float(initial_cash), "costs": costs}


def _validate_rebalance(rebalance: Mapping | None) -> dict:
    rebalance = dict(rebalance or {"frequency": "monthly"})
    unknown = set(rebalance) - {"frequency"}
    if unknown:
        raise ValueError(f"unknown rebalance key: {sorted(unknown)[0]}")
    frequency = rebalance.get("frequency", "monthly")
    if frequency not in calendar.SUPPORTED_FREQUENCIES:
        raise ValueError(f"rebalance.frequency must be one of {calendar.SUPPORTED_FREQUENCIES}")
    return {"frequency": frequency}


def _validate_no_candidate_policy(policy: str | None) -> str:
    policy = policy or "cash_hold"
    if policy in _RESERVED_NO_CANDIDATE_POLICIES:
        raise ValueError(f"no_candidate_policy={policy!r} is reserved for a future version and is not "
                         f"implemented in Stage H1; use 'cash_hold'")
    if policy not in IMPLEMENTED_NO_CANDIDATE_POLICIES:
        raise ValueError(f"unknown no_candidate_policy: {policy!r}")
    return policy


def _validate_components(conn, components: Any, allocation_method: str) -> list[dict]:
    if not isinstance(components, (list, tuple)) or not components:
        raise ValueError("components must be a non-empty list")
    out: list[dict] = []
    seen_strategy_ids: set[int] = set()
    for i, c in enumerate(components):
        if not isinstance(c, Mapping):
            raise ValueError(f"components[{i}] must be an object")
        strategy_id = c.get("strategy_id")
        job_id = c.get("walkforward_job_id")
        if not isinstance(strategy_id, int) or isinstance(strategy_id, bool):
            raise ValueError(f"components[{i}].strategy_id must be an integer")
        if not job_id or not isinstance(job_id, str):
            raise ValueError(f"components[{i}].walkforward_job_id is required")
        if strategy_id in seen_strategy_ids:
            raise ValueError(f"strategy_id {strategy_id} appears more than once in components "
                             f"(one component per strategy per portfolio version)")
        seen_strategy_ids.add(strategy_id)
        if not query_one(conn, "SELECT 1 x FROM strategies WHERE id=:i", i=strategy_id):
            raise ValueError(f"strategy {strategy_id} not found")
        try:
            job = wflab.get_job(job_id)
        except LookupError as exc:
            raise ValueError(f"components[{i}]: {exc}") from exc
        if job["status"] != "completed":
            raise ValueError(f"components[{i}]: walkforward job {job_id} is {job['status']}, not completed; "
                             f"a component needs completed OOS evidence")
        prov = job["provenance"]
        if prov.get("strategy_id") != strategy_id:
            raise ValueError(f"components[{i}]: walkforward job {job_id} belongs to strategy "
                             f"{prov.get('strategy_id')}, not {strategy_id}")
        if prov.get("strategy_version_id") is None:
            raise ValueError(f"components[{i}]: walkforward job {job_id} has no pinned strategy version "
                             f"provenance; cannot build a component contract on it")
        target_weight = c.get("target_weight")
        if allocation_method == "fixed_weight":
            if target_weight is None:
                target_weight = 0.0
            if isinstance(target_weight, bool) or not isinstance(target_weight, (int, float)):
                raise ValueError(f"components[{i}].target_weight must be a number for fixed_weight")
            target_weight = float(target_weight)
        elif target_weight is not None:
            raise ValueError(f"components[{i}].target_weight is only used by allocation_method=fixed_weight")
        out.append({
            "strategy_id": strategy_id, "strategy_version_id": prov["strategy_version_id"],
            "strategy_version_no": prov["strategy_version_no"], "walkforward_job_id": job_id,
            "target_weight": target_weight,
        })
    if allocation_method == "fixed_weight":
        # Re-uses the SAME validation the engine runs at every rebalance date, so a
        # portfolio can never be created with weights the engine would later reject.
        allocation.fixed_weight([str(c["strategy_id"]) for c in out],
                                {str(c["strategy_id"]): c["target_weight"] for c in out})
    return sorted(out, key=lambda c: c["strategy_id"])


def validate_definition(conn, payload: Mapping, *, defaults: Mapping | None = None) -> dict:
    """Validate a full portfolio version definition. ``defaults`` (an existing
    version's stored, already-validated definition) supplies any field the
    payload omits — same "partial update against the latest version" pattern
    ``strategy_lab.update_strategy`` uses."""
    defaults = defaults or {}
    method = payload.get("allocation_method", defaults.get("allocation_method"))
    if method not in allocation.ALLOCATION_METHODS:
        if method in allocation.NOT_YET_IMPLEMENTED_METHODS:
            raise ValueError(f"allocation_method={method!r} is capability/readiness-only in Stage H1 and has "
                             f"no runnable implementation yet")
        raise ValueError(f"unknown allocation_method: {method!r}")
    components = _validate_components(conn, payload.get("components", defaults.get("components")), method)
    allocation_config = payload.get("allocation_config", defaults.get("allocation_config", {}))
    if not isinstance(allocation_config, Mapping):
        raise ValueError("allocation_config must be an object")
    no_candidate_policy = _validate_no_candidate_policy(
        payload.get("no_candidate_policy", defaults.get("no_candidate_policy")))
    rebalance = _validate_rebalance(payload.get("rebalance", defaults.get("rebalance")))
    constraints = constraints_mod.validate_constraints(payload.get("constraints", defaults.get("constraints", {})))
    execution = _validate_execution(payload.get("execution", defaults.get("execution")))
    return {
        "components": components, "allocation_method": method, "allocation_config": dict(allocation_config),
        "no_candidate_policy": no_candidate_policy, "rebalance": rebalance,
        "constraints": constraints, "execution": execution,
    }


def _definition_hash(d: Mapping) -> str:
    return _hash({k: d[k] for k in ("components", "allocation_method", "allocation_config",
                                    "no_candidate_policy", "rebalance", "constraints", "execution")})


# ── strategy portfolios & versions ──────────────────────
def _append_version(conn, portfolio_id: int, d: Mapping, note: str | None) -> int:
    last = query_one(conn, "SELECT MAX(version_no) n FROM strategy_portfolio_versions WHERE portfolio_id=:p",
                     p=portfolio_id)["n"] or 0
    version_id = conn.execute(insert(t.strategy_portfolio_versions).values(
        portfolio_id=portfolio_id, version_no=last + 1, allocation_method=d["allocation_method"],
        allocation_config_json=_dumps(d["allocation_config"]), no_candidate_policy=d["no_candidate_policy"],
        rebalance_json=_dumps(d["rebalance"]), constraints_json=_dumps(d["constraints"]),
        execution_json=_dumps(d["execution"]), definition_hash=_definition_hash(d),
        note=note, created_at=_now())).inserted_primary_key[0]
    rows = [{"portfolio_version_id": version_id, **c} for c in d["components"]]
    conn.execute(insert(t.strategy_portfolio_components), rows)
    return version_id


def _components_view(conn, version_id: int) -> list[dict]:
    rows = query_all(conn, """SELECT c.*, s.name AS strategy_name, s.template_key
        FROM strategy_portfolio_components c JOIN strategies s ON s.id = c.strategy_id
        WHERE c.portfolio_version_id=:v ORDER BY c.strategy_id""", v=version_id)
    return rows


def _version_view(conn, row: Mapping) -> dict:
    return {
        "id": row["id"], "portfolio_id": row["portfolio_id"], "version_no": row["version_no"],
        "allocation_method": row["allocation_method"], "allocation_config": _loads(row["allocation_config_json"]),
        "no_candidate_policy": row["no_candidate_policy"], "rebalance": _loads(row["rebalance_json"]),
        "constraints": _loads(row["constraints_json"]), "execution": _loads(row["execution_json"]),
        "definition_hash": row["definition_hash"], "note": row["note"], "created_at": row["created_at"],
        "components": _components_view(conn, row["id"]),
    }


def _latest_version_row(conn, portfolio_id: int) -> dict | None:
    return query_one(conn, "SELECT * FROM strategy_portfolio_versions WHERE portfolio_id=:p "
                     "ORDER BY version_no DESC LIMIT 1", p=portfolio_id)


def _portfolio_view(conn, row: Mapping) -> dict:
    latest = _latest_version_row(conn, row["id"])
    return {**row, "archived": bool(row["archived"]),
            "latest_version": _version_view(conn, latest) if latest else None}


def create_portfolio(payload: Mapping) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    now = _now()
    with engine.begin() as conn:
        d = validate_definition(conn, payload)
        pid = conn.execute(insert(t.strategy_portfolios).values(
            name=name, description=payload.get("description"), archived=0,
            created_at=now, updated_at=now)).inserted_primary_key[0]
        _append_version(conn, pid, d, payload.get("note"))
        return get_portfolio(pid, conn)


def list_portfolios(include_archived: bool = False) -> list[dict]:
    with engine.connect() as conn:
        rows = query_all(conn, "SELECT * FROM strategy_portfolios " +
                         ("" if include_archived else "WHERE archived=0 ") + "ORDER BY id DESC")
        return [_portfolio_view(conn, r) for r in rows]


def get_portfolio(portfolio_id: int, conn=None) -> dict:
    def go(c):
        row = query_one(c, "SELECT * FROM strategy_portfolios WHERE id=:i", i=portfolio_id)
        if not row:
            raise LookupError(f"strategy portfolio {portfolio_id} not found")
        return _portfolio_view(c, row)
    if conn is not None:
        return go(conn)
    with engine.connect() as c:
        return go(c)


def update_portfolio(portfolio_id: int, payload: Mapping) -> dict:
    """Rename/describe in place; any versioned field appends a NEW version (only
    if the resulting, fully-resolved definition actually differs by hash)."""
    versioned_keys = {"components", "allocation_method", "allocation_config",
                      "no_candidate_policy", "rebalance", "constraints", "execution"}
    with engine.begin() as conn:
        row = query_one(conn, "SELECT * FROM strategy_portfolios WHERE id=:i", i=portfolio_id)
        if not row:
            raise LookupError(f"strategy portfolio {portfolio_id} not found")
        if row["archived"]:
            raise RuntimeError("strategy portfolio is archived")
        values: dict[str, Any] = {"updated_at": _now()}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise ValueError("name may not be empty")
            values["name"] = name
        if "description" in payload:
            values["description"] = payload["description"]
        if versioned_keys & set(payload):
            latest = _latest_version_row(conn, portfolio_id)
            defaults = _version_view(conn, latest)
            d = validate_definition(conn, payload, defaults=defaults)
            if _definition_hash(d) != latest["definition_hash"]:
                _append_version(conn, portfolio_id, d, payload.get("note"))
        conn.execute(update(t.strategy_portfolios).where(t.strategy_portfolios.c.id == portfolio_id)
                     .values(**values))
        return get_portfolio(portfolio_id, conn)


def archive_portfolio(portfolio_id: int) -> dict:
    """Soft delete: versions, components and research runs all stay — research
    history is never deleted by archiving."""
    with engine.begin() as conn:
        if not query_one(conn, "SELECT 1 x FROM strategy_portfolios WHERE id=:i", i=portfolio_id):
            raise LookupError(f"strategy portfolio {portfolio_id} not found")
        conn.execute(update(t.strategy_portfolios).where(t.strategy_portfolios.c.id == portfolio_id)
                     .values(archived=1, updated_at=_now()))
        return get_portfolio(portfolio_id, conn)


def list_versions(portfolio_id: int) -> list[dict]:
    with engine.connect() as conn:
        get_portfolio(portfolio_id, conn)
        return [_version_view(conn, r) for r in query_all(
            conn, "SELECT * FROM strategy_portfolio_versions WHERE portfolio_id=:p ORDER BY version_no DESC",
            p=portfolio_id)]


def get_version(portfolio_id: int, version_id: int) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM strategy_portfolio_versions WHERE id=:v AND portfolio_id=:p",
                        v=version_id, p=portfolio_id)
        if not row:
            raise LookupError(f"version {version_id} not found for strategy portfolio {portfolio_id}")
        return _version_view(conn, row)


def clone_portfolio(portfolio_id: int, payload: Mapping) -> dict:
    """Clone an existing portfolio (default: its latest version, or a specific
    ``version_id``) into a brand-new, independent portfolio identity. The clone
    starts as its own version 1; editing either portfolio afterwards never
    affects the other."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    with engine.begin() as conn:
        source = query_one(conn, "SELECT * FROM strategy_portfolios WHERE id=:i", i=portfolio_id)
        if not source:
            raise LookupError(f"strategy portfolio {portfolio_id} not found")
        if payload.get("version_id") is not None:
            version = query_one(conn, "SELECT * FROM strategy_portfolio_versions WHERE id=:v AND portfolio_id=:p",
                                v=payload["version_id"], p=portfolio_id)
            if not version:
                raise LookupError(f"version {payload['version_id']} not found for strategy portfolio {portfolio_id}")
        else:
            version = _latest_version_row(conn, portfolio_id)
            if not version:
                raise RuntimeError(f"strategy portfolio {portfolio_id} has no version to clone")
        d = _version_view(conn, version)
        components = [{"strategy_id": c["strategy_id"], "strategy_version_id": c["strategy_version_id"],
                      "strategy_version_no": c["strategy_version_no"], "walkforward_job_id": c["walkforward_job_id"],
                      "target_weight": c["target_weight"]} for c in d["components"]]
        definition = {"components": components, "allocation_method": d["allocation_method"],
                      "allocation_config": d["allocation_config"], "no_candidate_policy": d["no_candidate_policy"],
                      "rebalance": d["rebalance"], "constraints": d["constraints"], "execution": d["execution"]}
        now = _now()
        pid = conn.execute(insert(t.strategy_portfolios).values(
            name=name, description=payload.get("description", f"Cloned from portfolio {portfolio_id}"),
            archived=0, created_at=now, updated_at=now)).inserted_primary_key[0]
        _append_version(conn, pid, definition, f"cloned from portfolio {portfolio_id} version {version['version_no']}")
        return get_portfolio(pid, conn)


# ── research runs ────────────────────────────────────────
def _repro() -> dict:
    return {"research_engine_version": research_mod.RESEARCH_ENGINE_VERSION,
            "portfolio_lab_version": PORTFOLIO_LAB_VERSION}


def create_research_run(portfolio_id: int, payload: Mapping) -> dict:
    try:
        start = dt.date.fromisoformat(str(payload.get("start"))).isoformat()
        end = dt.date.fromisoformat(str(payload.get("end"))).isoformat()
    except ValueError as exc:
        raise ValueError("start and end must be ISO dates (YYYY-MM-DD)") from exc
    if start >= end:
        raise ValueError("start must be before end")
    with engine.begin() as conn:
        portfolio = query_one(conn, "SELECT * FROM strategy_portfolios WHERE id=:i", i=portfolio_id)
        if not portfolio:
            raise LookupError(f"strategy portfolio {portfolio_id} not found")
        if payload.get("version_id") is not None:
            version = query_one(conn, "SELECT * FROM strategy_portfolio_versions WHERE id=:v AND portfolio_id=:p",
                                v=payload["version_id"], p=portfolio_id)
            if not version:
                raise LookupError(f"version {payload['version_id']} not found for strategy portfolio {portfolio_id}")
        else:
            version = _latest_version_row(conn, portfolio_id)
            if not version:
                raise RuntimeError(f"strategy portfolio {portfolio_id} has no version to run")
        d = _version_view(conn, version)
        run_id = uuid.uuid4().hex
        request = {"start": start, "end": end}
        conn.execute(insert(t.portfolio_research_runs).values(
            id=run_id, portfolio_id=portfolio_id, portfolio_version_id=version["id"], status="queued",
            request_json=_dumps(request), components_json=_dumps(d["components"]),
            allocation_method=d["allocation_method"], allocation_config_json=_dumps(d["allocation_config"]),
            no_candidate_policy=d["no_candidate_policy"], constraints_json=_dumps(d["constraints"]),
            execution_json=_dumps(d["execution"]), repro_json=_dumps(_repro()), created_at=_now()))
    return {"run_id": run_id, "portfolio_version_id": version["id"], "status": "queued", "queued": True}


def _fail_run(run_id: str, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.portfolio_research_runs).where(
            (t.portfolio_research_runs.c.id == run_id) & t.portfolio_research_runs.c.status.in_(ACTIVE_RUN_STATUSES))
            .values(status="failed", error=message[:4000], finished_at=_now()))


def _cancel_requested(run_id: str) -> bool:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT cancel_requested FROM portfolio_research_runs WHERE id=:i", i=run_id)
    return bool(row and row["cancel_requested"])


def cancel_research_run(run_id: str) -> dict:
    """Cooperative cancel, same shape as ``optimization_lab.cancel_job`` /
    ``walkforward_lab.cancel_job``: a queued run is cancelled immediately (it
    never started). A running run is asked to stop at its next daily-replay
    checkpoint — this module's engine is a single monolithic pass with no
    per-fold/per-point granularity to preserve, so nothing is persisted for a
    cancelled run; it is simply re-run from its original pinned definition via
    ``resume_research_run``."""
    with engine.begin() as conn:
        row = query_one(conn, "SELECT status FROM portfolio_research_runs WHERE id=:i", i=run_id)
        if not row:
            raise LookupError(f"research run {run_id} not found")
        if row["status"] in TERMINAL_RUN_STATUSES:
            raise RuntimeError(f"run is already {row['status']}")
        if row["status"] == "queued":
            conn.execute(update(t.portfolio_research_runs).where(t.portfolio_research_runs.c.id == run_id)
                         .values(status="cancelled", finished_at=_now()))
        else:
            conn.execute(update(t.portfolio_research_runs).where(t.portfolio_research_runs.c.id == run_id)
                         .values(cancel_requested=1))
    return get_research_run(run_id)


def resume_research_run(run_id: str) -> dict:
    """Re-queue a failed/cancelled/interrupted run. There is no partial per-day
    progress to reuse -- ``execute_research_run`` only persists a run's
    equity/rebalance rows atomically at the very end of a successful pass -- so
    a resumed run recomputes the whole window from scratch, deterministically,
    against the SAME pinned portfolio_version_id/components/request it was
    created with (never a silently different definition; editing the portfolio
    afterwards appends a new version and never touches this run's pins)."""
    with engine.begin() as conn:
        row = query_one(conn, "SELECT status FROM portfolio_research_runs WHERE id=:i", i=run_id)
        if not row:
            raise LookupError(f"research run {run_id} not found")
        if row["status"] not in ("failed", "cancelled"):
            raise RuntimeError(f"run is {row['status']}; nothing to resume")
        conn.execute(update(t.portfolio_research_runs).where(t.portfolio_research_runs.c.id == run_id).values(
            status="queued", cancel_requested=0, error=None, started_at=None, finished_at=None))
    return {"run_id": run_id, "status": "queued", "queued": True}


def recover_interrupted() -> int:
    """Startup hook: the in-process worker died with the server (this was Stage
    H1's known gap -- ``portfolio_lab`` had no ``recover_interrupted()``, unlike
    ``strategy_lab``/``optimization_lab``/``walkforward_lab``, all three wired
    into ``app.main``'s lifespan). A queued/running row with no live worker can
    never finish; it becomes ``failed`` and resumable via
    ``resume_research_run`` -- never silently left stuck, never re-executed
    twice by a stray background task racing a fresh one (the atomic
    ``status='queued' -> 'running'`` claim in ``execute_research_run`` already
    prevents that)."""
    with engine.begin() as conn:
        return conn.execute(update(t.portfolio_research_runs).where(
            t.portfolio_research_runs.c.status.in_(ACTIVE_RUN_STATUSES))
            .values(status="failed", finished_at=_now(),
                    error="interrupted: server restarted before the run finished (resumable)")).rowcount


def execute_research_run(run_id: str) -> None:
    """Never raises; failures are recorded on the row (same contract as
    ``strategy_lab.execute_run`` / ``walkforward_lab.execute_job``)."""
    with engine.begin() as conn:
        claimed = conn.execute(update(t.portfolio_research_runs).where(
            (t.portfolio_research_runs.c.id == run_id) & (t.portfolio_research_runs.c.status == "queued"))
            .values(status="running", started_at=_now(), cancel_requested=0)).rowcount
        row = query_one(conn, "SELECT * FROM portfolio_research_runs WHERE id=:i", i=run_id)
    if not claimed:
        return
    try:
        request = _loads(row["request_json"])
        start, end = request["start"], request["end"]
        components = _loads(row["components_json"])
        execution = _loads(row["execution_json"])
        with engine.connect() as conn2:
            rebalance = _loads(query_one(conn2, "SELECT rebalance_json FROM strategy_portfolio_versions "
                                         "WHERE id=:v", v=row["portfolio_version_id"])["rebalance_json"])
        constraints = _loads(row["constraints_json"])
        method = row["allocation_method"]
        no_candidate_policy = row["no_candidate_policy"]
        component_keys = [str(c["strategy_id"]) for c in components]

        component_series: dict[str, dict] = {}
        for c in components:
            sd = load_strategy(str(c["strategy_id"]), c["walkforward_job_id"])
            component_series[str(c["strategy_id"])] = series_mod.build_component_series(sd, c["walkforward_job_id"])

        common_dates = series_mod.common_trading_dates(component_series, start, end)
        if not common_dates:
            raise ValueError("no common OOS trading dates across all pinned components in the requested window")
        reb_dates = calendar.rebalance_dates(common_dates, rebalance["frequency"])

        stock_ids: set[str] = set()
        for k in component_keys:
            for d in reb_dates:
                stock_ids |= set(component_series[k]["by_date"].get(d, {}).get("stock_weight_by_stock", {}))
        closes = closes_for(stock_ids, start, end)
        actions_rows = corporate_actions_for(stock_ids, start, end)
        actions_by_date: dict[str, list[dict]] = {}
        for a in actions_rows:
            actions_by_date.setdefault(a["date"], []).append(a)

        target_weight = ({str(c["strategy_id"]): c["target_weight"] for c in components}
                         if method == "fixed_weight" else None)
        cost_config = CostConfig(**(execution.get("costs") or {}))
        initial_cash = execution.get("initial_cash", DEFAULT_INITIAL_CASH)

        result = research_mod.run_portfolio_backtest(
            dates=common_dates, rebalance_dates=reb_dates, component_keys=component_keys,
            component_series=component_series, method=method, allocation_target_weight=target_weight,
            portfolio_constraints=constraints, no_candidate_policy=no_candidate_policy, closes=closes,
            actions_by_date=actions_by_date, cost_config=cost_config, initial_cash=initial_cash,
            cancel_check=lambda: _cancel_requested(run_id))

        if result["cancelled"]:
            # No equity/rebalance rows for a cancelled run -- this module only persists
            # a result atomically at the end of a full, successful pass (see
            # ``cancel_research_run``'s docstring); a stray, slower duplicate execution
            # racing a resume would lose the claim below (status already != 'running').
            with engine.begin() as conn:
                conn.execute(update(t.portfolio_research_runs).where(
                    (t.portfolio_research_runs.c.id == run_id) & (t.portfolio_research_runs.c.status == "running"))
                    .values(status="cancelled", finished_at=_now(), error=None))
            return

        curve = [{"date": r["date"], "net_equity": r["total_equity"], "gross_equity": r["total_equity"],
                 "benchmark": None} for r in result["daily"]]
        fills = [{"reference_notional": o["reference_notional"]}
                for rb in result["rebalances"] for o in rb["orders"].values()]
        metrics = strategy_metrics.compute(curve, closed_trades=[], fills=fills, risk_free_rate=0.0)
        total_cost = sum(rb["cost_total"] for rb in result["rebalances"])
        avg_equity = (sum(r["total_equity"] for r in result["daily"]) / len(result["daily"])
                     if result["daily"] else None)
        turnover_summary = {"total_cost": total_cost, "average_equity": avg_equity,
                            "cost_drag_pct": (total_cost / avg_equity * 100) if avg_equity else None,
                            "rebalance_count": len(result["rebalances"])}

        with engine.begin() as conn:
            equity_rows = [{"run_id": run_id, "date": r["date"], "cash": r["cash"],
                           "total_equity": r["total_equity"], "daily_return": r["daily_return"],
                           "cash_weight": r["cash_weight"]} for r in result["daily"]]
            for i in range(0, len(equity_rows), 500):
                conn.execute(insert(t.portfolio_research_equity), equity_rows[i:i + 500])
            reb_rows = [{"run_id": run_id, "date": rb["date"], "payload_json": _dumps(rb)}
                       for rb in result["rebalances"]]
            for i in range(0, len(reb_rows), 200):
                conn.execute(insert(t.portfolio_research_rebalances), reb_rows[i:i + 200])
            conn.execute(update(t.portfolio_research_runs).where(t.portfolio_research_runs.c.id == run_id).values(
                status="completed", finished_at=_now(), error=None,
                metrics_json=_dumps(metrics), warnings_json=_dumps(result["warnings"]),
                turnover_json=_dumps(turnover_summary)))
    except BaseException as exc:  # noqa: BLE001 - record any failure, then re-raise interrupts
        import traceback
        _fail_run(run_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}")
        if not isinstance(exc, Exception):
            raise


def _run_view(row: Mapping) -> dict:
    return {"id": row["id"], "portfolio_id": row["portfolio_id"], "portfolio_version_id": row["portfolio_version_id"],
            "status": row["status"], "error": row["error"], "request": _loads(row["request_json"]),
            "components": _loads(row["components_json"]), "allocation_method": row["allocation_method"],
            "allocation_config": _loads(row["allocation_config_json"]), "no_candidate_policy": row["no_candidate_policy"],
            "constraints": _loads(row["constraints_json"]), "reproducibility": _loads(row["repro_json"]),
            "created_at": row["created_at"], "started_at": row["started_at"], "finished_at": row["finished_at"]}


def get_research_run(run_id: str) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM portfolio_research_runs WHERE id=:i", i=run_id)
    if not row:
        raise LookupError(f"research run {run_id} not found")
    return _run_view(row)


def list_research_runs(portfolio_id: int, limit: int = 50) -> list[dict]:
    with engine.connect() as conn:
        get_portfolio(portfolio_id, conn)
        rows = query_all(conn, """SELECT id,portfolio_version_id,status,allocation_method,error,created_at,
            finished_at,metrics_json FROM portfolio_research_runs WHERE portfolio_id=:p
            ORDER BY created_at DESC, rowid DESC LIMIT :n""", p=portfolio_id, n=max(1, min(limit, 200)))
    return [{**{k: v for k, v in r.items() if k != "metrics_json"}, "metrics": _loads(r["metrics_json"])}
            for r in rows]


def get_research_result(run_id: str) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM portfolio_research_runs WHERE id=:i", i=run_id)
    if not row:
        raise LookupError(f"research run {run_id} not found")
    if row["status"] != "completed":
        raise RuntimeError(f"research run is {row['status']}; no result available")
    return {**_run_view(row), "metrics": _loads(row["metrics_json"]), "warnings": _loads(row["warnings_json"]),
            "turnover": _loads(row["turnover_json"])}


def get_research_equity(run_id: str) -> list[dict]:
    get_research_result(run_id)
    with engine.connect() as conn:
        return query_all(conn, "SELECT date,cash,total_equity,daily_return,cash_weight "
                         "FROM portfolio_research_equity WHERE run_id=:r ORDER BY date", r=run_id)


def get_research_rebalances(run_id: str) -> list[dict]:
    get_research_result(run_id)
    with engine.connect() as conn:
        rows = query_all(conn, "SELECT date,payload_json FROM portfolio_research_rebalances "
                         "WHERE run_id=:r ORDER BY date", r=run_id)
    return [_loads(r["payload_json"]) for r in rows]
