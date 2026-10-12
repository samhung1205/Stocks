"""Stage F1 real-data smoke: momentum_trend, formal DB, 2 complete folds (data_end 2025-10-31).

optimization -> freeze -> open_test_window -> OOS -> next fold -> stitched, with leakage checks.
Adds rows only (walkforward_* tables, optimization jobs, one strategy).

    uv run python scripts/f1_real_smoke.py
"""
import json
import sys
import time

from app.db import engine, query_all, query_one
from app.db.migrations import migrate
from app.services import optimization_lab as opt
from app.services import strategy_lab as lab
from app.services import walkforward_lab as wfl
from app.services.walkforward import freeze as wf_freeze

DATA_END = sys.argv[1] if len(sys.argv) > 1 else "2025-10-31"
t0 = time.time()
log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
migrate(engine)
BACKTEST = {"missing": {"execution": "partial"}}
strategy = lab.create_strategy({"name": "F1 real smoke (momentum_trend, partial exec)",
                                "template_key": "momentum_trend", "params": {}, "backtest": BACKTEST})
ver = strategy["latest_version"]
space = {"axes": {"momentum.lookback": [126, 189, 252], "trend.ma_lookback": [100, 200],
                  "selection.top_n": [20, 30]}}
events = []
_open, _oos_panel, _train_panel = wf_freeze.open_test_window, wfl.build_panel, opt.build_panel


def open_spy(fid):
    fz = wf_freeze.get_freeze(fid)
    events.append(("open_test_window", fz["train_end"], time.time() - t0))
    log("open_test_window", fz["train_end"])
    return _open(fid)


def oos_panel_spy(start, end, ids, **kw):
    events.append(("oos_panel", end, time.time() - t0))
    log("OOS panel build to", end, f"({len(ids)} ids)")
    return _oos_panel(start, end, ids, **kw)


def train_panel_spy(start, end, ids, **kw):
    events.append(("train_panel", end, time.time() - t0))
    log("training panel build to", end)
    return _train_panel(start, end, ids, **kw)


wf_freeze.open_test_window = open_spy
wfl.build_panel = oos_panel_spy
opt.build_panel = train_panel_spy
q = wfl.create_job({"template_key": "momentum_trend", "params": {}, "backtest": BACKTEST, "search_space": space,
                    "objective": "sharpe", "data_end": DATA_END,
                    "strategy_id": strategy["id"], "strategy_version_id": ver["id"]})
job = q["job_id"]
log("WF job", job, "folds", q["total_folds"], q.get("notes"))
assert q["total_folds"] >= 2, q
wfl.execute_job(job)
v = wfl.get_job(job)
log("status", v["status"], v["progress"], v["error"])
assert v["status"] == "completed", v["error"]

folds = wfl.get_folds(job)
with engine.connect() as c:
    for f in folds:
        i = f["index"]
        train_end, test_start, test_end = f["windows"]["train"][1], f["windows"]["test"][0], f["windows"]["test"][1]
        fp = json.loads(query_one(c, "SELECT repro_json j FROM optimization_jobs WHERE id=:i", i=f["train_job_id"])["j"])
        last_quote = fp["data_fingerprint"]["daily_quotes"]["last"]
        req = json.loads(query_one(c, "SELECT request_json j FROM optimization_jobs WHERE id=:i", i=f["train_job_id"])["j"])
        assert (req["research_start"], req["research_end"]) == tuple(f["windows"]["train"]), "train window"
        assert last_quote <= train_end, (last_quote, train_end)
        if f["outcome"] == "no_candidate":
            assert f["freeze_id"] is None and not f["oos"]["test_data_loaded"]
            assert not [e for e in events if e[0] == "oos_panel" and e[1] == test_end], "test data loaded for a cash fold"
            log(f"fold {i}: NO_CANDIDATE {f['freeze']['reasons']} -> cash, no test data loaded; "
                f"training_severity={f['severity']['training']['max_severity']}")
            continue
        t_train = next(e[2] for e in events if e[0] == "train_panel" and e[1] == train_end) if any(
            e[0] == "train_panel" and e[1] == train_end for e in events) else None
        t_open = next(e[2] for e in events if e[0] == "open_test_window" and e[1] == train_end)
        t_oos = next(e[2] for e in events if e[0] == "oos_panel" and e[1] == test_end)
        assert t_open < t_oos, "test window must open before the test-window panel is built"
        assert t_train is None or t_train < t_open  # None: training fully served from the run cache
        oos = f["oos"]
        assert oos["fills_before_test_start"] == 0 and oos["initial_state"] == "cash"
        assert oos["effective_first_fill_date"] is None or oos["effective_first_fill_date"] >= test_start
        log(f"fold {i}: outcome={f['outcome']} label={f['plateau_label']} primary={f['primary'] and f['primary']['params']} "
            f"edge(IS peak)={f['is_peak_edge_axes']} edge(primary)={f['primary_edge_axes']} "
            f"first_fill={oos['effective_first_fill_date']} fills={oos['n_fills']} unresolved={oos['unresolved_action_count']} "
            f"partial={oos['partial_rebalance_count']} severity={f['severity']['max_severity']} "
            f"training_severity={f['severity']['training']['max_severity']}")
        m, is_m = f["oos_metrics"], (f["is_training"] or {}).get("metrics")
        log(f"   OOS  sharpe={m.get('sharpe')} cagr={m.get('cagr')} mdd={m.get('max_drawdown')} turnover={m.get('turnover')} net={m.get('net_return')}")
        log(f"   IS   {is_m}")
        log(f"   order: train_panel@{t_train} open@{t_open:.0f} oos_panel@{t_oos:.0f}; training fingerprint last quote {last_quote} <= {train_end}")

s = wfl.get_stitched(job)
log("stitched available", s["available"], "window", s.get("window"))
log("stitched metrics", {k: s["metrics"].get(k) for k in ("net_return", "cagr", "sharpe", "max_drawdown", "turnover")})
log("stitched segments", s["segments"])
assert s["available"] and s["fills_before_first_test_start"] == 0 and not s["training_replay_used"]
ind0 = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "fold", 0)["points"]}
st = {p["date"]: p["net_equity"] for p in wfl.get_equity(job, "stitched")["points"]}
diff = max(abs(st[d] - e) / e for d, e in ind0.items())
log(f"stitched fold-0 segment vs independent fold 0: max rel diff {diff:.2e} (universe union differs: superset through last test_end)")
d = wfl.get_result(job)["diagnostics"]
log("degradation", json.dumps({k: d["degradation"][k]["delta_oos_minus_is"] for k in ("sharpe", "cagr")}, default=str)[:300])
log("stability", {k: d["parameter_stability"][k] for k in ("primary_changes", "candidate_digest_changes", "region_jaccard_consecutive")})
log("completeness", {k: d["completeness"][k] for k in ("planned_folds", "completed", "evaluated", "no_candidate", "complete")})
log("SMOKE OK", {"job": job, "strategy": strategy["id"]})
