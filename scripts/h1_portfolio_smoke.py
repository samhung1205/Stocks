"""Stage H1 real-data smoke: three Strategy Portfolios over the same five
completed Walk Forward jobs Stage F1.5 / Stage G / Stage H0 already validated.

    PYTHONPATH=. uv run python scripts/h1_portfolio_smoke.py

Writes output/h1/<portfolio>.json. Read-only against existing Walk Forward
evidence except for the new strategy_portfolios* / portfolio_research_* rows
this script itself creates (each run named "H1 smoke: ..." so it is easy to
find/archive afterwards).
"""
import json
import time
from pathlib import Path

from app.db import engine
from app.db.migrations import migrate
from app.services import portfolio_lab as plab
from app.services import walkforward_lab as wf

t0 = time.time()
log = lambda *a: print(f"[{time.time() - t0:6.1f}s]", *a, flush=True)  # noqa: E731
migrate(engine)

REAL_JOBS = {
    "momentum_trend": "9a3612c981e44cf88daabc655abc43ed",
    "quality_growth_momentum": "052d3472688740c89d8e4701bbc358cd",
    "revenue_growth_acceleration": "15f5a27ea6174a969ca5c2ebeeefe795",
    "value_quality": "098c6aa9252544cc88c910c68c05c490",
    "low_volatility_quality": "90f389547ad74c219c0074f5d2570b1d",
}
STRATEGY_ID = {k: wf.get_job(j)["provenance"]["strategy_id"] for k, j in REAL_JOBS.items()}
log("strategy_ids", STRATEGY_ID)

WINDOW = {"start": "2024-11-01", "end": "2026-09-18"}  # full F1.5 research window


def component(key, target_weight=None):
    c = {"strategy_id": STRATEGY_ID[key], "walkforward_job_id": REAL_JOBS[key]}
    if target_weight is not None:
        c["target_weight"] = target_weight
    return c


PORTFOLIOS = [
    {
        "name": "H1 smoke: Core Five",
        "description": "All five F1.5-validated strategies, equal weight.",
        "components": [component(k) for k in
                       ("momentum_trend", "quality_growth_momentum", "revenue_growth_acceleration",
                        "value_quality", "low_volatility_quality")],
        "allocation_method": "equal_weight",
    },
    {
        "name": "H1 smoke: Momentum + Value",
        "description": "Two strategies, 50/50 fixed weight.",
        "components": [component("momentum_trend", 0.5), component("value_quality", 0.5)],
        "allocation_method": "fixed_weight",
    },
    {
        "name": "H1 smoke: Custom Mix",
        "description": "Three strategies, non-equal fixed weights (0.4/0.35/0.15, 10% strategic cash).",
        "components": [component("momentum_trend", 0.4), component("revenue_growth_acceleration", 0.35),
                       component("low_volatility_quality", 0.15)],
        "allocation_method": "fixed_weight",
        "constraints": {"max_strategy_weight": 0.5, "max_single_stock_weight": 0.15},
    },
]

out_dir = Path("output/h1")
out_dir.mkdir(parents=True, exist_ok=True)

for spec in PORTFOLIOS:
    label = spec["name"].split(": ", 1)[1]
    log("creating", spec["name"])
    p = plab.create_portfolio(spec)
    run = plab.create_research_run(p["id"], WINDOW)
    plab.execute_research_run(run["run_id"])
    result = plab.get_research_result(run["run_id"])
    if result["error"]:
        log("FAILED", label, result["error"])
        continue
    equity = plab.get_research_equity(run["run_id"])
    rebalances = plab.get_research_rebalances(run["run_id"])
    log(label, "status", result["status"], "days", len(equity), "rebalances", len(rebalances),
       "metrics.cagr", result["metrics"].get("cagr"), "metrics.sharpe", result["metrics"].get("sharpe"))

    first, last = rebalances[0], rebalances[-1]
    holdings_summary = [
        {"date": rb["date"], "n_holdings": len(rb["holdings"]), "cash_weight": rb["cash_weight"],
         "total_equity": rb["total_equity"], "cost_total": rb["cost_total"],
         "n_orders": len(rb["orders"]), "deferred": len(rb["deferred"]),
         "constraint_diagnostics": rb["constraint_diagnostics"],
         "top_holdings": sorted(
             [{"stock_id": sid, **h} for sid, h in rb["holdings"].items() if h["weight"]],
             key=lambda h: -(h["weight"] or 0))[:5]}
        for rb in (first, rebalances[len(rebalances) // 2], last)
    ]
    overlap_count = sum(1 for rb in rebalances
                       for sid in rb["holdings"] if len(rb["contribution_by_stock"].get(sid, {})) > 1)
    no_candidate_cash_dates = [rb["date"] for rb in rebalances
                              if any(v > 0 for v in rb["target"]["no_candidate_cash_allocation"].values())]

    payload = {
        "portfolio": {"id": p["id"], "name": p["name"], "version_id": p["latest_version"]["id"],
                     "allocation_method": p["latest_version"]["allocation_method"],
                     "components": p["latest_version"]["components"], "constraints": p["latest_version"]["constraints"]},
        "run_id": run["run_id"], "window": WINDOW, "status": result["status"],
        "metrics": result["metrics"], "warnings": result["warnings"], "turnover": result["turnover"],
        "equity_days": len(equity), "equity_first": equity[0], "equity_last": equity[-1],
        "rebalance_count": len(rebalances),
        "rebalance_sample": holdings_summary,
        "overlap_stock_rebalance_incidences": overlap_count,
        "no_candidate_cash_rebalance_dates": no_candidate_cash_dates[:10],
        "no_candidate_cash_rebalance_count": len(no_candidate_cash_dates),
    }
    (out_dir / f"{label.lower().replace(' ', '_')}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str))

log("done")
