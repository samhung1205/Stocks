"""Stage G: read-only data boundary for cross-strategy analysis.

Only module in this package that touches the DB. It never writes anything and never
duplicates Walk Forward results into a new table — every call re-reads the existing
``walkforward_jobs`` / ``walkforward_folds`` / ``walkforward_equity`` / ``walkforward_trades``
rows through ``app.services.walkforward_lab``'s public accessors (same functions the
F2 UI uses), plus one direct, read-only lookup of ``stocks.industry`` for the current
(non point-in-time) sector taxonomy. Everything downstream in this package is pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from app.db import engine, query_all
from app.services import walkforward_lab as wf


@dataclass
class FoldData:
    index: int
    outcome: str            # "evaluated" | "no_candidate"
    windows: dict            # {"train": [start, end], "test": [start, end], "truncated": bool}
    oos_metrics: dict | None
    oos: dict | None
    severity: dict | None
    candidate_digest: str | None
    primary: dict | None
    equity: list[dict] = field(default_factory=list)   # [{date, net_equity, gross_equity, benchmark}]
    fills: list[dict] = field(default_factory=list)    # raw fill payloads (date, stock_id, side, shares, fill_price)


@dataclass
class StrategyData:
    key: str                 # e.g. "momentum_trend" — matches the template key
    job_id: str
    template_key: str
    provenance: dict
    diagnostics: dict | None   # wf_diag.analyze() summary (from job.summary_json), or None if not completed
    folds: dict[int, FoldData] = field(default_factory=dict)


def _load_fills(job_id: str, fold_index: int) -> list[dict]:
    """All 'fill' trades for one independent fold, paginated past the 2000-row per-call cap."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = wf.get_trades(job_id, "fold", fold_index=fold_index, kind="fill", limit=2000, offset=offset)
        rows.extend(page["rows"])
        offset += len(page["rows"])
        if offset >= page["total"] or not page["rows"]:
            break
    return rows


def load_strategy(key: str, job_id: str) -> StrategyData:
    """Load one strategy's completed Walk Forward job: folds, daily equity and fills.

    Raises LookupError if the job does not exist (surfaced to the caller, not swallowed,
    so a bad job id is a visible error rather than a silently empty strategy).
    """
    job = wf.get_job(job_id)
    result = wf.get_result(job_id)
    folds: dict[int, FoldData] = {}
    for f in wf.get_folds(job_id):
        idx = f["index"]
        if f["status"] != "completed":
            continue  # pending/failed/running folds carry no OOS evidence
        equity = wf.get_equity(job_id, "fold", fold_index=idx)["points"]
        fills = _load_fills(job_id, idx) if f["outcome"] == "evaluated" else []
        folds[idx] = FoldData(
            index=idx, outcome=f["outcome"], windows=f["windows"],
            oos_metrics=f["oos_metrics"], oos=f["oos"], severity=f["severity"],
            candidate_digest=f["candidate_digest"], primary=f["primary"],
            equity=equity, fills=fills)
    return StrategyData(key=key, job_id=job_id, template_key=job["template_key"],
                        provenance=job["provenance"], diagnostics=result["diagnostics"], folds=folds)


def load_all(job_map: Mapping[str, str]) -> tuple[dict[str, StrategyData], dict[str, str]]:
    """``job_map``: {strategy_key: job_id}. Returns (loaded, failed) — an unloadable job (missing
    id, bad job id, job not completed enough to have folds) is recorded in ``failed`` with a
    reason instead of raising, so the rest of the analysis still runs on whatever did load."""
    loaded: dict[str, StrategyData] = {}
    failed: dict[str, str] = {}
    for key, job_id in job_map.items():
        if not job_id:
            failed[key] = "no job_id provided"
            continue
        try:
            loaded[key] = load_strategy(key, job_id)
        except LookupError as exc:
            failed[key] = str(exc)
    return loaded, failed


def sector_map(stock_ids: set[str]) -> dict[str, str | None]:
    """Current (non point-in-time) ``stocks.industry`` for the given ids. See Stage C limitation:
    this is a present-day snapshot, never a historical/point-in-time taxonomy."""
    if not stock_ids:
        return {}
    with engine.connect() as conn:
        ids = sorted(stock_ids)
        placeholders = ",".join(f":id{i}" for i in range(len(ids)))
        rows = query_all(conn, f"SELECT stock_id, industry FROM stocks WHERE stock_id IN ({placeholders})",
                         **{f"id{i}": v for i, v in enumerate(ids)})
    found = {r["stock_id"]: r["industry"] for r in rows}
    return {sid: found.get(sid) for sid in stock_ids}
