"""Stage F0.5 real-data smoke: first-fold training job -> freeze -> provenance/digest ->
open test window -> revision blocked.  Uses the formal DB (additive rows only).

    uv run python scripts/f0_5_real_smoke.py
"""
import json
import sys
import time

from app.db import engine, query_all, query_one
from app.db.migrations import migrate
from app.services import optimization_lab as opt
from app.services import strategy_lab as lab
from app.services.optimization import robustness
from app.services.walkforward import freeze as wf_freeze
from app.services.walkforward import protocol as wf

t0 = time.time()
log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
migrate(engine)
with engine.connect() as c:
    sessions = [r["date"] for r in query_all(c, "SELECT date FROM trading_calendar WHERE date BETWEEN :s AND :e "
                                             "ORDER BY date", s=opt.RESEARCH_START, e=opt.RESEARCH_END)]
fold = wf.generate_folds(opt.RESEARCH_START, opt.RESEARCH_END, sessions)["folds"][0]
log("fold0", fold)
BACKTEST = {"missing": {"execution": "partial"}}
strategy = lab.create_strategy({"name": "F0.5 real smoke (momentum_trend, partial exec)",
                                "template_key": "momentum_trend", "params": {}, "backtest": BACKTEST})
ver = strategy["latest_version"]
space = {"axes": {"momentum.lookback": [126, 189, 252], "trend.ma_lookback": [100, 200],
                  "selection.top_n": [20, 30]}}
q = opt.create_job({
    "template_key": "momentum_trend", "params": {}, "backtest": BACKTEST, "search_space": space,
    "objective": "sharpe", "research_start": fold["train_start"], "research_end": fold["train_end"],
    "strategy_id": strategy["id"], "strategy_version_id": ver["id"]})
job = q["job_id"]
log("job", job, q)
opt.execute_job(job)
view = opt.get_job(job)
log("status", view["status"], view["progress"], "panel_loads", view["panel_loads"])
assert view["status"] == "completed", view["error"]
prov = view["provenance"]
assert prov["known"] and prov["strategy_id"] == strategy["id"] and prov["strategy_version_id"] == ver["id"]
assert prov["definition_hash"] == ver["definition_hash"] and prov["root_job_id"] == job and prov["revision_no"] == 0
assert view["request"]["research_end"] == fold["train_end"]
log("provenance ok", {k: prov[k] for k in ("strategy_id", "strategy_version_no", "definition_hash", "root_job_id")})

rob = opt.get_robustness(job)
plateau = rob["analysis"]["plateau"]
log("surface", plateau["label"], "region", plateau["region_size"], "edge", plateau["edge_of_search_range"],
    "severity", rob["severity"]["max_severity"])
strict = True
try:
    fz = wf_freeze.freeze_from_job(job, fold)
except ValueError as exc:
    log("strict freeze refused:", exc)
    strict = False
    fz = wf_freeze.freeze_from_job(job, fold, require_plateau=False)
log("freeze", fz["id"], "rule", fz["rule_version"], "strict", strict, "label", fz["plateau_label"],
    "primary", fz["primary_point_key"], "region_n", len(fz["region"]),
    "warnings", [w["code"] for w in fz["warnings"]])

# digest / provenance consistency: recompute the rule from the job's stored results
job_, view_, req, rows, done, points, topo, (name, key, higher) = opt._analysis_inputs(job, None)
analysis = robustness.analyze(points, topo, higher_is_better=higher)
valid = [p for p in points if isinstance(p["value"], (int, float))]
sel = wf_freeze.select_candidates(analysis, points, robustness.neighbour_graph(valid, topo),
                                  require_plateau=strict, higher_is_better=higher)
digest = wf_freeze.candidate_digest(fz["template_key"], fz["objective"], sel)
assert digest == fz["candidate_digest"], (digest, fz["candidate_digest"])
assert fz["root_job_id"] == job and fz["strategy_version_id"] == ver["id"] and fz["definition_hash"] == ver["definition_hash"]
assert fz["train_end"] == fold["train_end"] and fz["train_start"] == fold["train_start"]
assert sel["is_peak_key"] is not None
log("digest reproducible", digest[:16], "| IS peak", sel["is_peak_key"], "| primary", sel["primary_key"],
    "| medoid sums", sel["medoid_distance_sums"])
again = wf_freeze.freeze_from_job(job, fold, require_plateau=strict)
assert again["id"] == fz["id"] and again["created"] is False
log("idempotent ok")

# no test data in training: panel end == train_end; coverage last date <= train_end
cov = view["coverage"]
log("coverage", {k: cov.get(k) for k in ("panel_start", "research_start", "research_end")})
assert cov["research_end"] == fold["train_end"]
with engine.connect() as c:
    fp = json.loads(query_one(c, "SELECT repro_json j FROM optimization_jobs WHERE id=:i", i=job)["j"])["data_fingerprint"]
assert fp["daily_quotes"]["last"] <= fold["train_end"], fp["daily_quotes"]
log("fingerprint last quote date", fp["daily_quotes"]["last"], "<= train_end")

opened = wf_freeze.open_test_window(fz["id"])
assert opened["test_opened_at"]
log("test window opened", opened["test_opened_at"])
space2 = {"axes": {**space["axes"], "selection.top_n": [10, 20, 30]}}
try:
    opt.create_job({"template_key": "momentum_trend", "params": {}, "backtest": BACKTEST,
                    "search_space": space2, "objective": "sharpe", "research_start": fold["train_start"],
                    "research_end": fold["train_end"], "strategy_id": strategy["id"],
                    "strategy_version_id": ver["id"], "parent_job_id": job, "revision_reason": "smoke"})
    log("FAIL: revision was allowed")
    sys.exit(1)
except ValueError as exc:
    assert "TEST_WINDOW_ALREADY_OPENED" in str(exc)
    log("revision blocked:", str(exc)[:80])
with engine.connect() as c:
    n = query_one(c, "SELECT COUNT(*) n FROM optimization_jobs WHERE parent_job_id=:j", j=job)["n"]
assert n == 0
log("SMOKE OK", {"job": job, "freeze": fz["id"], "strategy_id": strategy["id"]})
