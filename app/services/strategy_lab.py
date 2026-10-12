"""Strategy Lab backend: strategy/version persistence, run lifecycle, result storage.

Sits above the pure ``app.services.strategy`` package (engine/metrics/templates
are used unchanged) and below the thin routes.

Guarantees:

* ``strategy_versions`` rows are immutable.  Editing a definition appends a new
  version; a run pins ``version_id`` and stores a self-contained snapshot of what
  it executed, so it never reads the (mutable) strategy row again.
* ``config_hash`` covers the resolved template config, backtest config, window,
  universe, publication mode, benchmark, engine version and a data fingerprint.
  Cache reuse is scoped to the same version and only ever returns a run whose
  hash matches exactly.
* A run becomes ``completed`` in the same transaction that writes its metrics,
  equity curve and trades.  Any failure leaves ``failed`` with no result rows.
* Publication mode, engine warnings and data coverage are stored as produced.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import platform
import sys
import traceback
import uuid
from dataclasses import fields
from typing import Any, Mapping

from sqlalchemy import delete, insert, update

from app.db import engine, query_all, query_one
from app.db import tables as t
from app.services import data_foundation
from app.services.strategy import run_backtest
from app.services.strategy.panel import build_panel
from app.services.strategy.signals import TemplateSignalRule, _deep_merge
from app.services.strategy.templates import TEMPLATES, get_template
from app.services.strategy.types import (
    BacktestConfig, CostConfig, ExecutionRule, MissingDataRule, PositionSizingRule,
)

# Bump when engine/metrics/factor semantics change; part of every config_hash.
ENGINE_VERSION = "strategy-core/stage-b+c+d-plus/2"

ACTIVE = ("queued", "running")
_BACKTEST_SECTIONS = {
    "execution": ExecutionRule, "missing": MissingDataRule,
    "sizing": PositionSizingRule, "costs": CostConfig,
}
_BACKTEST_SCALARS = ("initial_cash", "risk_free_rate")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None (metrics can legitimately be infinite)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_clean(v) for v in items]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dumps(value).encode()).hexdigest()


def _loads(text: str | None) -> Any:
    return json.loads(text) if text else None


# ── validation ──────────────────────────────────────────
def _check_keys(overrides: Mapping, default: Mapping, path: str = "params") -> None:
    if not isinstance(overrides, Mapping):
        raise ValueError(f"{path} must be an object")
    for key, value in overrides.items():
        if key not in default:
            raise ValueError(f"unknown {path} key: {key}")
        if isinstance(default[key], Mapping) and isinstance(value, Mapping):
            _check_keys(value, default[key], f"{path}.{key}")


def build_backtest_config(overrides: Mapping | None, template_key: str,
                          params: Mapping | None = None) -> BacktestConfig:
    """BacktestConfig from user overrides; rebalance is owned by the template."""
    overrides = dict(overrides or {})
    if "rebalance" in overrides:
        raise ValueError("backtest.rebalance is set by params.rebalance, not backtest")
    kwargs: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _BACKTEST_SCALARS:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"backtest.{key} must be a finite number")
            kwargs[key] = float(value)
        elif key in _BACKTEST_SECTIONS:
            cls = _BACKTEST_SECTIONS[key]
            if not isinstance(value, Mapping):
                raise ValueError(f"backtest.{key} must be an object")
            allowed = {f.name for f in fields(cls)}
            unknown = set(value) - allowed
            if unknown:
                raise ValueError(f"unknown backtest.{key} key: {sorted(unknown)[0]}")
            kwargs[key] = cls(**value)
        else:
            raise ValueError(f"unknown backtest key: {key}")
    try:
        return get_template(template_key).backtest_config(params, BacktestConfig(**kwargs))
    except (TypeError, KeyError) as exc:
        raise ValueError(f"invalid backtest/params: {exc}") from exc


def validate_definition(template_key: str, params: Mapping | None,
                        backtest: Mapping | None) -> tuple[dict, dict, dict]:
    """Return (params, backtest, resolved_template_config) or raise ValueError."""
    if template_key not in TEMPLATES:
        raise ValueError(f"unknown strategy template: {template_key}")
    template = TEMPLATES[template_key]
    params, backtest = dict(params or {}), dict(backtest or {})
    _check_keys(params, template.default_config)
    try:
        resolved = template.clone_config(params)
        json.loads(_dumps(resolved))  # must be JSON-serialisable and finite
        build_backtest_config(backtest, template_key, params)
    except (TypeError, KeyError) as exc:
        raise ValueError(f"invalid params: {exc}") from exc
    return params, backtest, resolved


# ── templates ───────────────────────────────────────────
def list_templates() -> list[dict]:
    return [tpl.metadata() for tpl in TEMPLATES.values()]


def get_template_meta(key: str) -> dict:
    if key not in TEMPLATES:
        raise LookupError(f"unknown strategy template: {key}")
    return TEMPLATES[key].metadata()


# ── strategies & versions ───────────────────────────────
def _version_row(row: Mapping) -> dict:
    return {**{k: v for k, v in row.items() if k not in ("params_json", "backtest_json")},
            "params": _loads(row["params_json"]), "backtest": _loads(row["backtest_json"])}


def _definition_hash(template_key: str, params: Mapping, backtest: Mapping) -> str:
    return _hash({"template_key": template_key, "params": params, "backtest": backtest})


def _append_version(conn, strategy_id: int, template_key: str, params: dict,
                    backtest: dict, note: str | None) -> int:
    last = query_one(conn, "SELECT MAX(version_no) n FROM strategy_versions WHERE strategy_id=:s",
                     s=strategy_id)["n"] or 0
    res = conn.execute(insert(t.strategy_versions).values(
        strategy_id=strategy_id, version_no=last + 1, template_key=template_key,
        params_json=_dumps(params), backtest_json=_dumps(backtest),
        definition_hash=_definition_hash(template_key, params, backtest),
        note=note, created_at=_now()))
    return res.inserted_primary_key[0]


def _latest_version(conn, strategy_id: int) -> dict | None:
    return query_one(conn, "SELECT * FROM strategy_versions WHERE strategy_id=:s "
                     "ORDER BY version_no DESC LIMIT 1", s=strategy_id)


def _strategy_view(conn, row: Mapping) -> dict:
    latest = _latest_version(conn, row["id"])
    return {**row, "archived": bool(row["archived"]),
            "latest_version": _version_row(latest) if latest else None}


def create_strategy(payload: Mapping) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    template_key = payload.get("template_key")
    params, backtest, _ = validate_definition(template_key, payload.get("params"),
                                              payload.get("backtest"))
    now = _now()
    with engine.begin() as conn:
        sid = conn.execute(insert(t.strategies).values(
            name=name, description=payload.get("description"), template_key=template_key,
            archived=0, created_at=now, updated_at=now)).inserted_primary_key[0]
        _append_version(conn, sid, template_key, params, backtest, payload.get("note"))
        return get_strategy(sid, conn)


def list_strategies(include_archived: bool = False) -> list[dict]:
    with engine.connect() as conn:
        rows = query_all(conn, "SELECT * FROM strategies " +
                         ("" if include_archived else "WHERE archived=0 ") + "ORDER BY id DESC")
        return [_strategy_view(conn, r) for r in rows]


def get_strategy(strategy_id: int, conn=None) -> dict:
    def go(c):
        row = query_one(c, "SELECT * FROM strategies WHERE id=:i", i=strategy_id)
        if not row:
            raise LookupError(f"strategy {strategy_id} not found")
        return _strategy_view(c, row)
    if conn is not None:
        return go(conn)
    with engine.connect() as c:
        return go(c)


def update_strategy(strategy_id: int, payload: Mapping) -> dict:
    """Rename/describe in place; a changed definition appends a NEW version."""
    with engine.begin() as conn:
        row = query_one(conn, "SELECT * FROM strategies WHERE id=:i", i=strategy_id)
        if not row:
            raise LookupError(f"strategy {strategy_id} not found")
        if row["archived"]:
            raise RuntimeError("strategy is archived")
        if "template_key" in payload and payload["template_key"] != row["template_key"]:
            raise ValueError("template_key cannot change; create a new strategy")
        values: dict[str, Any] = {"updated_at": _now()}
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise ValueError("name may not be empty")
            values["name"] = name
        if "description" in payload:
            values["description"] = payload["description"]
        if "params" in payload or "backtest" in payload:
            latest = _latest_version(conn, strategy_id)
            params, backtest, _ = validate_definition(
                row["template_key"],
                payload["params"] if "params" in payload else _loads(latest["params_json"]),
                payload["backtest"] if "backtest" in payload else _loads(latest["backtest_json"]))
            if _definition_hash(row["template_key"], params, backtest) != latest["definition_hash"]:
                _append_version(conn, strategy_id, row["template_key"], params, backtest,
                                payload.get("note"))
        conn.execute(update(t.strategies).where(t.strategies.c.id == strategy_id).values(**values))
        return get_strategy(strategy_id, conn)


def archive_strategy(strategy_id: int) -> dict:
    """Soft delete: versions and runs stay so old results remain reproducible."""
    with engine.begin() as conn:
        if not query_one(conn, "SELECT 1 x FROM strategies WHERE id=:i", i=strategy_id):
            raise LookupError(f"strategy {strategy_id} not found")
        conn.execute(update(t.strategies).where(t.strategies.c.id == strategy_id)
                     .values(archived=1, updated_at=_now()))
        return get_strategy(strategy_id, conn)


def list_versions(strategy_id: int) -> list[dict]:
    with engine.connect() as conn:
        get_strategy(strategy_id, conn)
        return [_version_row(r) for r in query_all(
            conn, "SELECT * FROM strategy_versions WHERE strategy_id=:s ORDER BY version_no DESC",
            s=strategy_id)]


def get_version(strategy_id: int, version_id: int) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM strategy_versions WHERE id=:v AND strategy_id=:s",
                        v=version_id, s=strategy_id)
    if not row:
        raise LookupError(f"version {version_id} not found for strategy {strategy_id}")
    return _version_row(row)


# ── data fingerprint / coverage ─────────────────────────
def data_fingerprint(conn, stock_ids: list[str], start: str, end: str) -> dict:
    """Cheap change detector for the rows a run would read (not a proof of equality)."""
    ids = sorted(stock_ids)
    binds = {f"s{i}": sid for i, sid in enumerate(ids)}
    in_sql = ",".join(f":s{i}" for i in range(len(ids)))
    end_excl = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    span = dict(start=start, end=end, **binds)

    def one(sql, **extra):
        return query_all(conn, sql, **{**span, **extra})[0]

    fp = {
        "daily_quotes": one(f"""SELECT COUNT(*) n, MIN(date) first, MAX(date) last,
            ROUND(SUM(COALESCE(close,0)),4) close_sum, ROUND(SUM(COALESCE(open,0)),4) open_sum,
            ROUND(SUM(COALESCE(amount,0)),0) amount_sum FROM daily_quotes
            WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end"""),
        "valuations": one(f"""SELECT COUNT(*) n, MAX(date) last, ROUND(SUM(COALESCE(pe,0)),4) pe_sum,
            ROUND(SUM(COALESCE(pb,0)),4) pb_sum FROM valuations
            WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end"""),
        "corporate_actions": one(f"""SELECT COUNT(*) n, ROUND(SUM(COALESCE(cash_dividend,0)),4) cash_sum
            FROM corporate_actions WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end"""),
        "benchmark_series": one("""SELECT COUNT(*) n, ROUND(SUM(value),4) value_sum FROM benchmark_series
            WHERE date BETWEEN :start AND :end"""),
        "trading_calendar": one("SELECT COUNT(*) n FROM trading_calendar WHERE date BETWEEN :start AND :end"),
        "stocks": _hash(query_all(conn, f"""SELECT stock_id,listed_date,delisted_date,type,industry
            FROM stocks WHERE stock_id IN ({in_sql}) ORDER BY stock_id""", **binds)),
    }
    for table, col in (("month_revenue", "revenue"), ("financials", "net_income")):
        fp[table] = query_all(conn, f"""SELECT publish_source, COUNT(*) n, MAX(publish_date) last,
            ROUND(SUM(COALESCE({col},0)),4) value_sum FROM {table}
            WHERE stock_id IN ({in_sql}) AND publish_date < :end_excl
            GROUP BY publish_source ORDER BY publish_source""", end_excl=end_excl, **binds)
    return fp


def panel_coverage(panel, requested_ids: list[str]) -> dict:
    """What the run actually had to work with, derived from the panel it used."""
    sessions = [d for d in panel.days if d.is_trading_session]
    bars_per_stock = {sid: 0 for sid in requested_ids}
    publications: dict[str, dict[str, int]] = {"month_revenue": {}, "financials": {}}
    valuation_obs = valuation_days = benchmark_days = actions = 0
    for day in panel.days:
        for sid in day.bars:
            if sid in bars_per_stock:
                bars_per_stock[sid] += 1
        for pub in day.publications:
            bucket = publications[pub.dataset]
            bucket[pub.publish_source] = bucket.get(pub.publish_source, 0) + 1
        valuation_obs += len(day.valuations)
        valuation_days += 1 if day.valuations else 0
        benchmark_days += 1 if day.benchmark is not None else 0
        actions += len(day.actions)
    counts = sorted(bars_per_stock.values())
    without = sorted(sid for sid, n in bars_per_stock.items() if n == 0)
    notes = []
    if without:
        notes.append(f"{len(without)} requested stocks have no quotes in the window")
    if not any(publications["month_revenue"].values()) and not any(publications["financials"].values()):
        notes.append(f"no {panel.publication_mode}-mode fundamental publications available")
    if benchmark_days == 0:
        notes.append(f"benchmark {panel.benchmark_id} has no data in the window")
    return {
        "publication_mode": panel.publication_mode,
        "benchmark_id": panel.benchmark_id,
        "first_date": panel.days[0].date if panel.days else None,
        "last_date": panel.days[-1].date if panel.days else None,
        "panel_days": len(panel.days),
        "trading_sessions": len(sessions),
        "stocks_requested": len(requested_ids),
        "stocks_with_quotes": len(requested_ids) - len(without),
        "stocks_without_quotes": without[:50],
        "quote_days_min": counts[0] if counts else 0,
        "quote_days_median": counts[len(counts) // 2] if counts else 0,
        "quote_days_max": counts[-1] if counts else 0,
        "publications_visible": publications,
        "valuation_days": valuation_days,
        "valuation_observations": valuation_obs,
        "benchmark_days": benchmark_days,
        "corporate_actions": actions,
        "notes": notes,
    }


# ── runs ────────────────────────────────────────────────
def _resolve_universe(conn, payload: Mapping, start: str) -> tuple[list[str], dict]:
    ids = payload.get("stock_ids")
    if ids:
        if not isinstance(ids, (list, tuple)):
            raise ValueError("stock_ids must be a list")
        return list(dict.fromkeys(str(s) for s in ids)), {"kind": "explicit"}
    spec = payload.get("universe") or {}
    if not isinstance(spec, Mapping):
        raise ValueError("universe must be an object")
    min_amount = spec.get("min_avg_amount", 30_000_000)
    min_obs = spec.get("min_observations", 120)
    rows = data_foundation.liquid_universe(conn, spec.get("as_of") or start, min_amount, min_obs)
    if not rows:
        raise ValueError("liquid universe is empty for the requested window; backfill data first "
                         "or pass stock_ids")
    return [r["stock_id"] for r in rows], {
        "kind": "liquid_universe", "as_of": spec.get("as_of") or start,
        "min_avg_amount": min_amount, "min_observations": min_obs,
        "note": "Trailing year before as_of; stocks table is a current snapshot, "
                "not a historical constituent list."}


def _repro() -> dict:
    return {"engine_version": ENGINE_VERSION, "python": sys.version.split()[0],
            "platform": platform.platform(), "template_catalog": sorted(TEMPLATES)}


def _hash_inputs(request: Mapping, fingerprint: Mapping) -> dict:
    return {"engine_version": ENGINE_VERSION, "data_fingerprint": fingerprint,
            "request": {k: request[k] for k in (
                "template_key", "template_config", "backtest_config", "start", "end",
                "stock_ids", "publication_mode", "benchmark_id")}}


def create_run(strategy_id: int, payload: Mapping) -> dict:
    """Queue a run (or reuse an identical one). Caller schedules ``execute_run``."""
    force = bool(payload.get("force"))
    mode = payload.get("publication_mode", "strict")
    if mode not in ("strict", "assumed"):
        raise ValueError("publication_mode must be strict or assumed")
    try:
        start = dt.date.fromisoformat(str(payload.get("start"))).isoformat()
        end = dt.date.fromisoformat(str(payload.get("end"))).isoformat()
    except ValueError as exc:
        raise ValueError("start and end must be ISO dates (YYYY-MM-DD)") from exc
    if start >= end:
        raise ValueError("start must be before end")
    benchmark_id = payload.get("benchmark_id", "TAIEX_TR")

    with engine.connect() as conn:
        strategy = query_one(conn, "SELECT * FROM strategies WHERE id=:i", i=strategy_id)
        if not strategy:
            raise LookupError(f"strategy {strategy_id} not found")
        if strategy["archived"]:
            raise RuntimeError("strategy is archived")
        if payload.get("version_id") is not None:
            version = query_one(conn, "SELECT * FROM strategy_versions WHERE id=:v AND strategy_id=:s",
                                v=payload["version_id"], s=strategy_id)
            if not version:
                raise LookupError(f"version {payload['version_id']} not found for strategy {strategy_id}")
        else:
            version = _latest_version(conn, strategy_id)
        params, backtest = _loads(version["params_json"]), _loads(version["backtest_json"])
        # Re-validate: a template change since the version was saved must not queue a doomed run.
        params, backtest, resolved = validate_definition(version["template_key"], params, backtest)
        config = build_backtest_config(backtest, version["template_key"], params)
        stock_ids, universe = _resolve_universe(conn, payload, start)
        fingerprint = data_fingerprint(conn, stock_ids, start, end)
        request = {
            "template_key": version["template_key"], "params": params, "backtest": backtest,
            "template_config": resolved, "backtest_config": config.to_dict(),
            "start": start, "end": end, "stock_ids": stock_ids, "universe": universe,
            "publication_mode": mode, "benchmark_id": benchmark_id,
            "strategy_version_no": version["version_no"],
            "definition_hash": version["definition_hash"],
        }
        config_hash = _hash(_hash_inputs(request, fingerprint))
        if not force:
            hit = query_one(conn, """SELECT id,status FROM backtest_runs
                WHERE version_id=:v AND config_hash=:h AND status IN ('completed','queued','running')
                ORDER BY CASE status WHEN 'completed' THEN 0 ELSE 1 END, created_at DESC LIMIT 1""",
                            v=version["id"], h=config_hash)
            if hit:
                return {"run_id": hit["id"], "status": hit["status"], "cache_hit": True,
                        "config_hash": config_hash, "queued": False}

    run_id = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(insert(t.backtest_runs).values(
            id=run_id, strategy_id=strategy_id, version_id=version["id"], status="queued",
            config_hash=config_hash, request_json=_dumps(request),
            repro_json=_dumps({**_repro(), "data_fingerprint": fingerprint}),
            publication_mode=mode, created_at=_now()))
    return {"run_id": run_id, "status": "queued", "cache_hit": False,
            "config_hash": config_hash, "queued": True}


def _fail(run_id: str, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(update(t.backtest_runs).where(
            (t.backtest_runs.c.id == run_id) & t.backtest_runs.c.status.in_(ACTIVE))
            .values(status="failed", error=message[:4000], finished_at=_now()))


def _persist(conn, run_id: str, result: dict, coverage: dict) -> None:
    conn.execute(delete(t.backtest_equity).where(t.backtest_equity.c.run_id == run_id))
    conn.execute(delete(t.backtest_trades).where(t.backtest_trades.c.run_id == run_id))
    equity = [{"run_id": run_id, "date": p["date"], "net_equity": _clean(p["net_equity"]),
               "gross_equity": _clean(p["gross_equity"]), "benchmark": _clean(p["benchmark"])}
              for p in result["equity_curve"]]
    for i in range(0, len(equity), 500):
        conn.execute(insert(t.backtest_equity), equity[i:i + 500])
    trades = [{"run_id": run_id, "kind": kind, "seq": seq, "payload_json": _dumps(row)}
              for kind, rows in (("fill", result["fills"]), ("closed", result["closed_trades"]))
              for seq, row in enumerate(rows)]
    for i in range(0, len(trades), 500):
        conn.execute(insert(t.backtest_trades), trades[i:i + 500])
    # Last statement of the transaction: completed only if everything above stuck.
    conn.execute(update(t.backtest_runs).where(t.backtest_runs.c.id == run_id).values(
        status="completed", finished_at=_now(), error=None,
        metrics_json=_dumps(result["metrics"]),
        warnings_json=_dumps(result["warnings"]),
        coverage_json=_dumps(coverage),
        metadata_json=_dumps(result["metadata"]),
        open_positions_json=_dumps(result["open_positions"]),
        ending_cash=_clean(result["ending_cash"])))


def execute_run(run_id: str) -> None:
    """Run a queued backtest to completion. Never raises; failures are recorded."""
    with engine.begin() as conn:
        claimed = conn.execute(update(t.backtest_runs).where(
            (t.backtest_runs.c.id == run_id) & (t.backtest_runs.c.status == "queued"))
            .values(status="running", started_at=_now())).rowcount
        row = query_one(conn, "SELECT * FROM backtest_runs WHERE id=:i", i=run_id)
    if not claimed:
        return
    try:
        request = _loads(row["request_json"])
        # Execute from the frozen snapshot only -- never from mutable strategy/template state.
        config = _config_from_snapshot(request)
        rule = TemplateSignalRule(request["template_key"], request["template_config"], None)
        with engine.connect() as conn:
            fingerprint = data_fingerprint(conn, request["stock_ids"], request["start"], request["end"])
        repro = _loads(row["repro_json"]) or {}
        drifted = fingerprint != repro.get("data_fingerprint")
        panel = build_panel(request["start"], request["end"], request["stock_ids"],
                            publication_mode=request["publication_mode"],
                            benchmark_id=request["benchmark_id"], db_engine=engine)
        result = run_backtest(panel, rule, config)
        if result["metadata"]["publication_mode"] != request["publication_mode"]:
            raise RuntimeError("publication_mode mismatch between request and engine result")
        coverage = panel_coverage(panel, request["stock_ids"])
        with engine.begin() as conn:
            if drifted:
                # Data changed between queueing and execution: record what was really used.
                conn.execute(update(t.backtest_runs).where(t.backtest_runs.c.id == run_id).values(
                    config_hash=_hash(_hash_inputs(request, fingerprint)),
                    repro_json=_dumps({**repro, "data_fingerprint": fingerprint,
                                       "data_changed_since_queued": True})))
            _persist(conn, run_id, result, coverage)
    except BaseException as exc:  # noqa: BLE001 - record any failure, then re-raise interrupts
        _fail(run_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}")
        if not isinstance(exc, Exception):
            raise


def _config_from_snapshot(request: Mapping) -> BacktestConfig:
    return build_backtest_config(request["backtest"], request["template_key"], request["params"])


def recover_interrupted() -> int:
    """Startup hook: a queued/running row with no live worker can never finish."""
    with engine.begin() as conn:
        return conn.execute(update(t.backtest_runs).where(t.backtest_runs.c.status.in_(ACTIVE))
                            .values(status="failed", finished_at=_now(),
                                    error="interrupted: server restarted before the run finished")).rowcount


def _run_view(row: Mapping) -> dict:
    return {
        "id": row["id"], "strategy_id": row["strategy_id"], "version_id": row["version_id"],
        "status": row["status"], "config_hash": row["config_hash"],
        "publication_mode": row["publication_mode"], "error": row["error"],
        "created_at": row["created_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "request": _loads(row["request_json"]), "reproducibility": _loads(row["repro_json"]),
    }


def get_run(run_id: str) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM backtest_runs WHERE id=:i", i=run_id)
    if not row:
        raise LookupError(f"run {run_id} not found")
    return _run_view(row)


def list_runs(strategy_id: int, limit: int = 50) -> list[dict]:
    with engine.connect() as conn:
        get_strategy(strategy_id, conn)
        rows = query_all(conn, """SELECT id,version_id,status,config_hash,publication_mode,error,
            created_at,finished_at,metrics_json FROM backtest_runs WHERE strategy_id=:s
            ORDER BY created_at DESC, rowid DESC LIMIT :n""", s=strategy_id, n=max(1, min(limit, 200)))
    return [{**{k: v for k, v in r.items() if k != "metrics_json"},
             "metrics": _loads(r["metrics_json"])} for r in rows]


def get_result(run_id: str) -> dict:
    with engine.connect() as conn:
        row = query_one(conn, "SELECT * FROM backtest_runs WHERE id=:i", i=run_id)
    if not row:
        raise LookupError(f"run {run_id} not found")
    if row["status"] != "completed":
        raise RuntimeError(f"run is {row['status']}; no result available")
    return {**_run_view(row),
            "metrics": _loads(row["metrics_json"]), "warnings": _loads(row["warnings_json"]),
            "coverage": _loads(row["coverage_json"]), "metadata": _loads(row["metadata_json"]),
            "open_positions": _loads(row["open_positions_json"]), "ending_cash": row["ending_cash"]}


def get_equity(run_id: str) -> list[dict]:
    get_result(run_id)  # 404/409 gate
    with engine.connect() as conn:
        return query_all(conn, "SELECT date,net_equity,gross_equity,benchmark FROM backtest_equity "
                         "WHERE run_id=:r ORDER BY date", r=run_id)


def get_trades(run_id: str, kind: str = "closed", limit: int = 500, offset: int = 0) -> dict:
    if kind not in ("fill", "closed"):
        raise ValueError("kind must be fill or closed")
    get_result(run_id)
    with engine.connect() as conn:
        total = query_one(conn, "SELECT COUNT(*) n FROM backtest_trades WHERE run_id=:r AND kind=:k",
                          r=run_id, k=kind)["n"]
        rows = query_all(conn, """SELECT payload_json FROM backtest_trades WHERE run_id=:r AND kind=:k
            ORDER BY seq LIMIT :n OFFSET :o""", r=run_id, k=kind,
                         n=max(1, min(limit, 5000)), o=max(0, offset))
    return {"kind": kind, "total": total, "offset": max(0, offset),
            "rows": [json.loads(r["payload_json"]) for r in rows]}
