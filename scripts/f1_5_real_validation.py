"""Stage F1.5 real-data Walk Forward validation of ONE template (formal DB, additive rows only).

Fixed contract: default protocol (expanding, 24M/6M/6M, final >=3M kept), default search space, default
objective, assumed fundamentals, missing.execution=partial, plateau-medoid freeze.  No tuning.

    PYTHONPATH=. uv run python scripts/f1_5_real_validation.py momentum_trend
Writes output/f1_5/<template>.json
"""
import json
import sys
import time
from pathlib import Path

from app.db import engine, query_all, query_one
from app.db.migrations import migrate
from app.services import optimization_lab as opt
from app.services import strategy_lab as lab
from app.services import walkforward_lab as wfl

key = sys.argv[1]
t0 = time.time()
log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
migrate(engine)
BACKTEST = {"missing": {"execution": "partial"}}
strategy = lab.create_strategy({"name": f"F1.5 validation ({key}, partial exec)", "template_key": key,
                                "params": {}, "backtest": BACKTEST})
ver = strategy["latest_version"]
q = wfl.create_job({"template_key": key, "params": {}, "backtest": BACKTEST, "strategy_id": strategy["id"],
                    "strategy_version_id": ver["id"]})
job = q["job_id"]
log("job", job, "folds", q["total_folds"])
wfl.execute_job(job)
runtime = time.time() - t0
v = wfl.get_job(job)
log("status", v["status"], v["progress"], (v["error"] or "")[:300])
folds = wfl.get_folds(job)
res = wfl.get_result(job)
diag = res["diagnostics"]

# ── A. job ──
cache = {"runs_total": 0, "runs_cached": 0, "panel_loads": 0}
train_cov = []
with engine.connect() as c:
    for f in folds:
        if not f["train_job_id"]:
            continue
        tj = opt.get_job(f["train_job_id"])
        cache["runs_total"] += tj["progress"]["total"]
        cache["runs_cached"] += tj["progress"]["cached"]
        cache["panel_loads"] += tj["panel_loads"]
        rows = opt._run_rows(c, f["train_job_id"], "completed")
        cov = [r["diagnostics"].get("coverage") or {} for r in rows]
        sig = sum(x.get("signals_in_window") or 0 for x in cov)
        empty = sum(x.get("signals_empty") or 0 for x in cov)
        med = sorted(x["selected_median"] for x in cov if x.get("selected_median") is not None)
        firsts = sorted(x["first_fill_date"] for x in cov if x.get("first_fill_date"))
        unres_runs = sum(1 for r in rows if (r["diagnostics"].get("unresolved_action_count_in_window") or 0) > 0)
        train_cov.append({"fold": f["index"], "runs_completed": len(rows), "runs_failed": tj["progress"]["failed"],
                          "signals_in_window": sig, "signals_empty": empty,
                          "selected_median_of_medians": med[len(med) // 2] if med else None,
                          "earliest_first_fill": firsts[0] if firsts else None,
                          "latest_first_fill": firsts[-1] if firsts else None,
                          "runs_with_unresolved_in_window": unres_runs, "universe_size": (tj["universe"] or {}).get("union_size")})
job_rec = {"job_id": job, "template": key, "status": v["status"], "planned_folds": v["progress"]["total"],
           "completed_folds": v["progress"]["completed"], "failed_folds": v["progress"]["failed"],
           "evaluated": v["progress"]["evaluated"], "no_candidate": v["progress"]["no_candidate"],
           "runtime_seconds": round(runtime), "cache": cache, "publication_mode": v["provenance"]["publication_mode"],
           "execution_policy": v["provenance"]["execution_policy"], "candidate_rule": v["provenance"]["candidate_rule"],
           "protocol_version": v["provenance"]["protocol_version"], "search_grid": v["request"]["search"]["grid_size"]}

# ── B. per fold ──
rows_out = []
for f in folds:
    m, isx = f["oos_metrics"] or {}, (f["is_training"] or {})
    im = isx.get("metrics") or {}
    oos = f["oos"] or {}
    sev = f["severity"] or {}
    tsev = sev.get("training") or {}
    codes = {c["code"]: c["count"] for c in sev.get("codes", [])}
    tcodes = {c["code"]: c["count"] for c in (tsev.get("codes") or [])}
    rows_out.append({
        "fold": f["index"], "train": f["windows"]["train"], "test": f["windows"]["test"],
        "truncated": f["windows"]["truncated"], "outcome": f["outcome"], "plateau_label": f["plateau_label"],
        "region_size": len(f["region"] or []), "primary": (f["primary"] or {}).get("params"),
        "is_peak_differs_from_medoid": (None if not f["primary"] else isx.get("is_peak_key") != f["primary"]["point_key"]),
        "is_peak_edge_axes": f["is_peak_edge_axes"], "primary_on_search_edge": f["candidate_flags"]["primary_on_edge"],
        "primary_edge_axes": f["primary_edge_axes"], "first_oos_fill": oos.get("effective_first_fill_date"),
        "is": {k: im.get(k) for k in ("sharpe", "cagr", "max_drawdown", "turnover")},
        "oos": {k: m.get(k) for k in ("sharpe", "cagr", "max_drawdown", "turnover", "cost_drag_pct", "net_return")},
        "unresolved_action_count": oos.get("unresolved_action_count"),
        "partial_rebalance_count": oos.get("partial_rebalance_count"),
        "severity": sev.get("max_severity"), "severity_codes": codes,
        "training_severity": tsev.get("max_severity"), "training_severity_codes": tcodes,
        "no_candidate_reasons": (f["freeze"] or {}).get("reasons") if f["outcome"] == "no_candidate" else None,
        "candidate_digest": f["candidate_digest"], "fills": oos.get("n_fills"),
        "universe": f.get("universe")})
    log(f"fold {f['index']} {f['windows']['test']} {f['outcome']} label={f['plateau_label']} "
        f"primary={(f['primary'] or {}).get('params')} sev={sev.get('max_severity')}/{tsev.get('max_severity')}")

# ── C. cross fold ──
ev = [r for r in rows_out if r["outcome"] == "evaluated"]
n = len(rows_out)
cross = {
    "degradation": {k: diag["degradation"][k] for k in ("sharpe", "cagr", "max_drawdown", "turnover")},
    "parameter_stability": diag["parameter_stability"],
    "candidate_digest_changes": diag["parameter_stability"]["candidate_digest_changes"],
    "region_jaccard": diag["parameter_stability"]["region_jaccard_consecutive"],
    "no_candidate_frequency": f"{job_rec['no_candidate']}/{n}",
    "is_peak_edge_frequency": f"{sum(1 for r in ev if r['is_peak_edge_axes'])}/{len(ev)}",
    "primary_on_edge_frequency": f"{sum(1 for r in ev if r['primary_on_search_edge'])}/{len(ev)}",
    "folds_with_unresolved_oos": f"{sum(1 for r in ev if r['unresolved_action_count'])}/{len(ev)}",
    "folds_with_partial_rebalance": f"{sum(1 for r in ev if r['partial_rebalance_count'])}/{len(ev)}",
    "fold_max_severity": {r["fold"]: r["severity"] for r in rows_out},
    "critical_codes_oos": sorted({c for r in rows_out for c in r["severity_codes"]
                                  if c in ("UNRESOLVED_ACTIONS_IN_WINDOW", "NO_STABLE_CANDIDATE", "ALL_SIGNALS_EMPTY",
                                           "UNRESOLVED_CORPORATE_ACTION", "DELISTING_EXIT_UNAVAILABLE", "NO_VALUATION_PRICE",
                                           "EXECUTION_CANCELLED", "INVALID_SHARE_MULTIPLIER")}),
    "critical_codes_training": sorted({c for r in rows_out for c in r["training_severity_codes"]
                                       if c in ("UNRESOLVED_CORPORATE_ACTION", "UNRESOLVED_ACTIONS_IN_WINDOW", "ALL_SIGNALS_EMPTY",
                                                "DELISTING_EXIT_UNAVAILABLE", "NO_VALUATION_PRICE", "EXECUTION_CANCELLED",
                                                "INVALID_SHARE_MULTIPLIER", "NO_STABLE_CANDIDATE")})}

# ── D. independent vs stitched ──
st = wfl.get_stitched(job)
d = {"available": st["available"], "evidence": st.get("evidence"), "independent": st.get("independent"),
     "training_replay_used": st.get("training_replay_used"),
     "fills_before_first_test_start": st.get("fills_before_first_test_start"),
     "metrics": {k: (st.get("metrics") or {}).get(k) for k in ("net_return", "cagr", "sharpe", "max_drawdown", "turnover")},
     "segments": st.get("segments"), "severity_codes": [c["code"] for c in (st.get("severity") or {}).get("codes", [])],
     "universe": st.get("universe")}
checks = {}
if st["available"]:
    s_eq = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "stitched")["points"]}
    first_eval = next((r["fold"] for r in rows_out if r["outcome"] == "evaluated"), None)
    if rows_out[0]["outcome"] == "evaluated":
        i0 = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "fold", 0)["points"]}
        checks["fold0_max_rel_diff"] = max(abs(s_eq[x] - e) / e for x, e in i0.items())
    fills = wfl.get_trades(job, "stitched", kind="fill", limit=2000)["rows"]
    total = wfl.get_trades(job, "stitched", kind="fill", limit=1)["total"]
    if total > len(fills):
        fills = []
        for off in range(0, total, 2000):
            fills += wfl.get_trades(job, "stitched", kind="fill", limit=2000, offset=off)["rows"]
    checks["stitched_fills_total"] = total
    checks["stitched_fills_before_test_start"] = sum(1 for x in fills if x["date"] < folds[0]["windows"]["test"][0])
    nc = []
    for r in rows_out:
        if r["outcome"] != "no_candidate":
            continue
        a, b = r["test"]
        win = [x for x in fills if a <= x["date"] <= b]
        pts = sorted((p for p in wfl.get_equity(job, "stitched")["points"] if a <= p["date"] <= b), key=lambda p: p["date"])
        after = {p["net_equity"] for p in pts[1:]}
        seg = next(x for x in st["segments"] if x["fold"] == r["fold"])
        nc.append({"fold": r["fold"], "fills": len(win), "all_sells": all(x["side"] == "sell" for x in win),
                   "all_on_first_session": {x["date"] for x in win} <= {a}, "inherited_positions": seg["inherited_position_count"],
                   "equity_constant_after_first_session": len(after) <= 1})
    checks["no_candidate_liquidation"] = nc
truncated = [r for r in rows_out if r["truncated"]]
special = {"final_truncated_fold": [{"fold": r["fold"], "test": r["test"], "outcome": r["outcome"],
                                     "has_PARTIAL_FOLD_TEST_WINDOW": "PARTIAL_FOLD_TEST_WINDOW" in r["severity_codes"]} for r in truncated],
           "training_coverage": train_cov}
out = {"job": job_rec, "folds": rows_out, "cross_fold": cross, "stitched": d, "checks": checks, "special": special}
Path("output/f1_5").mkdir(parents=True, exist_ok=True)
Path(f"output/f1_5/{key}.json").write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False))
log("DONE", key, "runtime", job_rec["runtime_seconds"], "status", v["status"])
