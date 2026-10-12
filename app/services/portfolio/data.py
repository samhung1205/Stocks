"""Stage H0: read-only DB boundary for the exact sleeve ledger.

Only module in this package that touches the DB. Reuses ``cross_strategy.data`` for
job/fold/equity/fill loading (same rows the F2 UI and Stage G already read) and adds
the two extra reads the exact ledger needs that Stage G never needed: corporate
actions and daily closing prices, plus a job's ``initial_cash`` (from its stored
request, via the same ``strategy_lab.build_backtest_config`` the engine itself uses
to build it — never a hardcoded default).
"""
from __future__ import annotations

from app.db import engine, query_all
from app.services import strategy_lab as lab
from app.services import walkforward_lab as wf
from app.services.cross_strategy.data import FoldData, StrategyData, load_strategy

__all__ = ["FoldData", "StrategyData", "load_strategy", "initial_cash_for_job",
           "corporate_actions_for", "closes_for"]


def initial_cash_for_job(job_id: str) -> float:
    job = wf.get_job(job_id)
    req = job["request"]
    config = lab.build_backtest_config(req.get("backtest"), req["template_key"], req.get("params"))
    return config.initial_cash


def _in_clause(ids: list[str]) -> tuple[str, dict]:
    return ",".join(f":id{i}" for i in range(len(ids))), {f"id{i}": v for i, v in enumerate(ids)}


def corporate_actions_for(stock_ids: set[str], start: str, end: str) -> list[dict]:
    """Corporate-action rows for the given stocks, restricted to ``[start, end]`` — an
    independent OOS fold starts from cash at ``start`` (=test_start), so nothing before
    it can affect the fold's ledger."""
    if not stock_ids:
        return []
    placeholders, binds = _in_clause(sorted(stock_ids))
    with engine.connect() as conn:
        return query_all(conn, f"""SELECT stock_id, date, kind, cash_dividend, share_multiplier
            FROM corporate_actions WHERE stock_id IN ({placeholders}) AND date BETWEEN :start AND :end
            ORDER BY date, stock_id""", start=start, end=end, **binds)


def closes_for(stock_ids: set[str], start: str, end: str) -> dict[str, dict[str, float]]:
    """``{stock_id: {date: close}}`` for the given stocks within ``[start, end]`` — the raw
    (unadjusted) close, the same series the engine marks equity with. Only dates with a
    non-null close are included; the caller forward-fills."""
    if not stock_ids:
        return {}
    placeholders, binds = _in_clause(sorted(stock_ids))
    with engine.connect() as conn:
        rows = query_all(conn, f"""SELECT stock_id, date, close FROM daily_quotes
            WHERE stock_id IN ({placeholders}) AND date BETWEEN :start AND :end AND close IS NOT NULL
            ORDER BY date""", start=start, end=end, **binds)
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["stock_id"], {})[r["date"]] = r["close"]
    return out
