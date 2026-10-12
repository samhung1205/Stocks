"""Stage D1 backend tests: temp SQLite DB, no network, no real data."""
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db import query_all, query_one, upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services import strategy_lab as lab

START, END = "2025-01-02", "2025-02-28"
SMALL = {  # shrink lookbacks so a two-month synthetic DB can trade
    "universe": {"liquidity_lookback": 3, "min_liquidity_observations": 3,
                 "min_average_daily_amount": 1},
    "momentum": {"lookback": 5, "skip_recent": 1},
    "trend": {"ma_lookback": 5},
    "selection": {"top_n": 2},
    "rebalance": {"frequency": "weekly"},
}
STOCKS = ["1101", "1102", "1103"]  # ordinary-share shaped ids (Stage D+ classifier)


def _dates():
    d, out = dt.date.fromisoformat(START), []
    while d.isoformat() <= END:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


class LabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/lab.db")
        migrate(self.engine)
        self.patch = patch.object(lab, "engine", self.engine)
        self.patch.start()
        self.dates = _dates()
        with self.engine.begin() as c:
            upsert_many(c, t.stocks, [{"stock_id": s, "name": s, "market": "TWSE", "type": "stock",
                                       "industry": "X"} for s in STOCKS])
            upsert_many(c, t.trading_calendar, [{"date": d} for d in self.dates])
            slope = {"1101": 1.0, "1102": .5, "1103": -.2}
            upsert_many(c, t.daily_quotes, [
                {"stock_id": s, "date": d, "open": 100 + slope[s] * i, "close": 100 + slope[s] * i,
                 "high": 101 + slope[s] * i, "low": 99 + slope[s] * i, "volume": 1e6, "amount": 1e8}
                for s in STOCKS for i, d in enumerate(self.dates)])

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def strategy(self, **extra):
        return lab.create_strategy({"name": "s", "template_key": "momentum_trend",
                                    "params": SMALL, **extra})

    def run_payload(self, **extra):
        return {"start": START, "end": END, "stock_ids": STOCKS, **extra}

    def run_now(self, sid, **extra):
        q = lab.create_run(sid, self.run_payload(**extra))
        if q["queued"]:
            lab.execute_run(q["run_id"])
        return q

    def count(self, table):
        with self.engine.connect() as c:
            return query_one(c, f"SELECT COUNT(*) n FROM {table}")["n"]


class VersionTests(LabCase):
    def test_edit_appends_version_and_old_run_keeps_snapshot(self):
        s = self.strategy()
        v1 = s["latest_version"]["id"]
        q = self.run_now(s["id"])
        before = lab.get_run(q["run_id"])
        s2 = lab.update_strategy(s["id"], {"params": {"selection": {"top_n": 1}}})
        self.assertEqual(s2["latest_version"]["version_no"], 2)
        after = lab.get_run(q["run_id"])
        self.assertEqual(after["version_id"], v1)
        self.assertEqual(after["request"], before["request"])
        self.assertEqual(after["request"]["params"]["selection"]["top_n"], 2)
        # v1 row itself is untouched
        self.assertEqual(lab.get_version(s["id"], v1)["params"]["selection"]["top_n"], 2)
        self.assertEqual(lab.get_version(s["id"], v1)["definition_hash"],
                         s["latest_version"]["definition_hash"])

    def test_rename_and_identical_definition_do_not_add_versions(self):
        s = self.strategy()
        lab.update_strategy(s["id"], {"name": "renamed"})
        lab.update_strategy(s["id"], {"params": SMALL})
        self.assertEqual(len(lab.list_versions(s["id"])), 1)

    def test_new_run_after_edit_uses_new_version_and_pin_works(self):
        s = self.strategy()
        v1 = s["latest_version"]["id"]
        s2 = lab.update_strategy(s["id"], {"params": {"selection": {"top_n": 1}}})
        latest = self.run_now(s["id"])
        pinned = self.run_now(s["id"], version_id=v1)
        self.assertEqual(lab.get_run(latest["run_id"])["version_id"], s2["latest_version"]["id"])
        self.assertEqual(lab.get_run(pinned["run_id"])["version_id"], v1)
        self.assertNotEqual(latest["config_hash"], pinned["config_hash"])
        with self.assertRaises(LookupError):
            lab.create_run(s["id"], self.run_payload(version_id=9999))

    def test_invalid_definitions_rejected(self):
        with self.assertRaises(ValueError):
            lab.create_strategy({"name": "x", "template_key": "nope"})
        with self.assertRaises(ValueError):
            lab.create_strategy({"name": "x", "template_key": "momentum_trend",
                                 "params": {"typo_key": 1}})
        with self.assertRaises(ValueError):
            lab.create_strategy({"name": "x", "template_key": "momentum_trend",
                                 "backtest": {"initial_cash": -5}})
        with self.assertRaises(ValueError):
            lab.create_strategy({"name": "x", "template_key": "momentum_trend",
                                 "backtest": {"rebalance": {"frequency": "daily"}}})


class CacheTests(LabCase):
    def test_same_config_reuses_completed_run(self):
        s = self.strategy()
        first = self.run_now(s["id"])
        self.assertEqual(lab.get_run(first["run_id"])["status"], "completed")
        again = lab.create_run(s["id"], self.run_payload())
        self.assertTrue(again["cache_hit"])
        self.assertFalse(again["queued"])
        self.assertEqual(again["run_id"], first["run_id"])
        self.assertEqual(self.count("backtest_runs"), 1)

    def test_force_creates_new_run_with_same_hash(self):
        s = self.strategy()
        first = self.run_now(s["id"])
        forced = self.run_now(s["id"], force=True)
        self.assertNotEqual(forced["run_id"], first["run_id"])
        self.assertEqual(forced["config_hash"], first["config_hash"])

    def test_config_changes_produce_new_run(self):
        s = self.strategy()
        base = self.run_now(s["id"])
        variants = [dict(end="2025-02-27"), dict(publication_mode="assumed"),
                    dict(stock_ids=["1101", "1102"]), dict(benchmark_id="OTHER")]
        hashes = {base["config_hash"]}
        for v in variants:
            q = self.run_now(s["id"], **v)
            self.assertFalse(q["cache_hit"], v)
            hashes.add(q["config_hash"])
        self.assertEqual(len(hashes), 1 + len(variants))
        # backtest-config change via a new version
        lab.update_strategy(s["id"], {"backtest": {"costs": {"slippage_bps": 25}}})
        q = self.run_now(s["id"])
        self.assertFalse(q["cache_hit"])
        self.assertNotIn(q["config_hash"], hashes)

    def test_data_change_invalidates_cache(self):
        s = self.strategy()
        first = self.run_now(s["id"])
        with self.engine.begin() as c:
            upsert_many(c, t.daily_quotes, [{"stock_id": "1101", "date": self.dates[3], "close": 555.0}])
        q = lab.create_run(s["id"], self.run_payload())
        self.assertFalse(q["cache_hit"])
        self.assertNotEqual(q["config_hash"], first["config_hash"])

    def test_failed_run_is_never_reused(self):
        s = self.strategy()
        with patch.object(lab, "run_backtest", side_effect=RuntimeError("boom")):
            bad = self.run_now(s["id"])
        self.assertEqual(lab.get_run(bad["run_id"])["status"], "failed")
        again = lab.create_run(s["id"], self.run_payload())
        self.assertFalse(again["cache_hit"])
        self.assertNotEqual(again["run_id"], bad["run_id"])

    def test_active_duplicate_is_deduplicated(self):
        s = self.strategy()
        a = lab.create_run(s["id"], self.run_payload())
        b = lab.create_run(s["id"], self.run_payload())
        self.assertTrue(b["cache_hit"])
        self.assertEqual(a["run_id"], b["run_id"])


class PersistenceTests(LabCase):
    def test_completed_run_stores_everything(self):
        s = self.strategy()
        q = self.run_now(s["id"], publication_mode="assumed")
        res = lab.get_result(q["run_id"])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["publication_mode"], "assumed")
        self.assertEqual(res["metadata"]["publication_mode"], "assumed")
        self.assertEqual(res["coverage"]["publication_mode"], "assumed")
        self.assertEqual(res["coverage"]["stocks_requested"], 3)
        self.assertEqual(res["coverage"]["trading_sessions"], len(self.dates))
        self.assertIn("engine_version", res["reproducibility"])
        self.assertIn("data_fingerprint", res["reproducibility"])
        self.assertEqual(len(lab.get_equity(q["run_id"])), len(self.dates))
        fills = lab.get_trades(q["run_id"], "fill")
        self.assertGreater(fills["total"], 0)
        self.assertEqual(len(fills["rows"]), fills["total"])
        self.assertEqual(lab.get_trades(q["run_id"], "fill", limit=1)["rows"], fills["rows"][:1])

    def test_warnings_and_metadata_match_engine_output_verbatim(self):
        s = lab.create_strategy({"name": "r", "template_key": "revenue_growth_acceleration",
                                 "params": {"universe": SMALL["universe"], "selection": {"top_n": 2},
                                            "rebalance": {"frequency": "weekly"}}})
        captured = {}
        real = lab.run_backtest

        def spy(*a, **k):
            captured["result"] = real(*a, **k)
            return captured["result"]
        with patch.object(lab, "run_backtest", side_effect=spy):
            q = self.run_now(s["id"], publication_mode="assumed")
        res = lab.get_result(q["run_id"])
        self.assertEqual(res["warnings"], captured["result"]["warnings"])
        self.assertTrue(any("assumed publication mode" in w for w in res["warnings"]))
        self.assertEqual(res["metadata"], json.loads(json.dumps(captured["result"]["metadata"],
                                                                default=sorted)))
        # strict run keeps its own mode and carries no assumed-mode warning
        strict = lab.get_result(self.run_now(s["id"])["run_id"])
        self.assertEqual(strict["publication_mode"], "strict")
        self.assertFalse(any("assumed publication mode" in w for w in strict["warnings"]))

    def test_infinite_metrics_are_stored_as_valid_json(self):
        s = self.strategy()
        real = lab.run_backtest

        def inf(*a, **k):
            r = real(*a, **k)
            r["metrics"]["profit_factor"] = float("inf")
            return r
        with patch.object(lab, "run_backtest", side_effect=inf):
            q = self.run_now(s["id"])
        self.assertIsNone(lab.get_result(q["run_id"])["metrics"]["profit_factor"])


class FailureTests(LabCase):
    def test_engine_failure_leaves_failed_and_no_results(self):
        s = self.strategy()
        with patch.object(lab, "run_backtest", side_effect=RuntimeError("engine exploded")):
            q = self.run_now(s["id"])
        run = lab.get_run(q["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("engine exploded", run["error"])
        self.assertEqual(self.count("backtest_equity"), 0)
        self.assertEqual(self.count("backtest_trades"), 0)
        with self.assertRaises(RuntimeError):
            lab.get_result(q["run_id"])
        with self.assertRaises(RuntimeError):
            lab.get_equity(q["run_id"])
        with self.engine.connect() as c:
            row = query_one(c, "SELECT metrics_json,warnings_json,coverage_json FROM backtest_runs")
        self.assertEqual(tuple(row.values()), (None, None, None))

    def test_persist_failure_rolls_back_partial_rows(self):
        s = self.strategy()
        real_insert = lab.insert

        def insert(table, *a, **k):  # equity rows go in first; trades insert then fails
            if table is t.backtest_trades:
                raise RuntimeError("disk full")
            return real_insert(table, *a, **k)
        with patch.object(lab, "insert", side_effect=insert):
            q = self.run_now(s["id"])
        run = lab.get_run(q["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("disk full", run["error"])
        self.assertEqual(self.count("backtest_equity"), 0)
        self.assertEqual(self.count("backtest_trades"), 0)

    def test_panel_failure_is_recorded(self):
        s = self.strategy()
        with patch.object(lab, "build_panel", side_effect=ValueError("no panel")):
            q = self.run_now(s["id"])
        self.assertEqual(lab.get_run(q["run_id"])["status"], "failed")

    def test_recover_interrupted_marks_active_runs_failed(self):
        s = self.strategy()
        a = lab.create_run(s["id"], self.run_payload())
        self.assertEqual(lab.recover_interrupted(), 1)
        run = lab.get_run(a["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("interrupted", run["error"])
        lab.execute_run(a["run_id"])  # a failed run must never be resurrected
        self.assertEqual(lab.get_run(a["run_id"])["status"], "failed")

    def test_execute_is_single_shot(self):
        s = self.strategy()
        q = self.run_now(s["id"])
        equity = self.count("backtest_equity")
        lab.execute_run(q["run_id"])
        self.assertEqual(self.count("backtest_equity"), equity)

    def test_runs_execute_from_snapshot_not_mutable_state(self):
        s = self.strategy()
        q = lab.create_run(s["id"], self.run_payload())
        lab.update_strategy(s["id"], {"params": {"selection": {"top_n": 1}}})
        with self.engine.begin() as c:  # even direct tampering with the strategy row is irrelevant
            c.execute(t.strategies.update().values(name="tampered"))
        lab.execute_run(q["run_id"])
        run = lab.get_run(q["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["request"]["params"]["selection"]["top_n"], 2)

    def test_no_universe_is_a_400_class_error(self):
        s = self.strategy()
        with self.assertRaises(ValueError):
            lab.create_run(s["id"], {"start": START, "end": END})
        with self.assertRaises(ValueError):
            lab.create_run(s["id"], self.run_payload(publication_mode="bogus"))
        with self.assertRaises(ValueError):
            lab.create_run(s["id"], {"start": END, "end": START, "stock_ids": STOCKS})


class ApiTests(LabCase):
    def setUp(self):
        super().setUp()
        from app.api.routes import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_templates(self):
        r = self.client.get("/api/strategy-lab/templates")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 5)
        self.assertEqual(self.client.get("/api/strategy-lab/templates/momentum_trend").status_code, 200)
        self.assertEqual(self.client.get("/api/strategy-lab/templates/none").status_code, 404)

    def test_crud(self):
        c = self.client
        body = {"name": "My strat", "template_key": "momentum_trend", "params": SMALL}
        r = c.post("/api/strategy-lab/strategies", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["id"]
        self.assertEqual(c.post("/api/strategy-lab/strategies", json={"name": "", "template_key": "x"}).status_code, 400)
        self.assertEqual(c.get(f"/api/strategy-lab/strategies/{sid}").json()["name"], "My strat")
        r = c.patch(f"/api/strategy-lab/strategies/{sid}", json={"params": {"selection": {"top_n": 1}}})
        self.assertEqual(r.json()["latest_version"]["version_no"], 2)
        self.assertEqual(len(c.get(f"/api/strategy-lab/strategies/{sid}/versions").json()), 2)
        self.assertEqual(c.get(f"/api/strategy-lab/strategies/{sid}/versions/{r.json()['latest_version']['id']}").status_code, 200)
        self.assertEqual(c.get("/api/strategy-lab/strategies/999").status_code, 404)
        self.assertEqual(c.patch(f"/api/strategy-lab/strategies/{sid}", json={"params": {"bad": 1}}).status_code, 400)
        self.assertEqual(c.delete(f"/api/strategy-lab/strategies/{sid}").json()["archived"], True)
        self.assertEqual(c.get("/api/strategy-lab/strategies").json(), [])
        self.assertEqual(len(c.get("/api/strategy-lab/strategies?include_archived=true").json()), 1)
        self.assertEqual(c.patch(f"/api/strategy-lab/strategies/{sid}", json={"name": "n"}).status_code, 409)
        self.assertEqual(c.post(f"/api/strategy-lab/strategies/{sid}/runs", json=self.run_payload()).status_code, 409)

    def test_backtest_lifecycle(self):
        c = self.client
        sid = c.post("/api/strategy-lab/strategies",
                     json={"name": "s", "template_key": "momentum_trend", "params": SMALL}).json()["id"]
        r = c.post(f"/api/strategy-lab/strategies/{sid}/runs", json=self.run_payload())
        self.assertEqual(r.status_code, 200, r.text)
        run_id = r.json()["run_id"]
        self.assertTrue(r.json()["queued"])
        # TestClient ran the background task before returning
        self.assertEqual(c.get(f"/api/strategy-lab/runs/{run_id}").json()["status"], "completed")
        res = c.get(f"/api/strategy-lab/runs/{run_id}/result")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["publication_mode"], "strict")
        self.assertGreater(len(c.get(f"/api/strategy-lab/runs/{run_id}/equity").json()), 0)
        trades = c.get(f"/api/strategy-lab/runs/{run_id}/trades?kind=fill").json()
        self.assertGreater(trades["total"], 0)
        self.assertEqual(c.get(f"/api/strategy-lab/runs/{run_id}/trades?kind=zzz").status_code, 400)
        again = c.post(f"/api/strategy-lab/strategies/{sid}/runs", json=self.run_payload()).json()
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["run_id"], run_id)
        listed = c.get(f"/api/strategy-lab/strategies/{sid}/runs").json()
        self.assertEqual([x["id"] for x in listed], [run_id])
        self.assertEqual(c.get("/api/strategy-lab/runs/nope").status_code, 404)
        self.assertEqual(c.post(f"/api/strategy-lab/strategies/{sid}/runs", json={"start": "x"}).status_code, 400)

    def test_failed_run_via_api_has_no_result(self):
        c = self.client
        sid = c.post("/api/strategy-lab/strategies",
                     json={"name": "s", "template_key": "momentum_trend", "params": SMALL}).json()["id"]
        with patch.object(lab, "run_backtest", side_effect=RuntimeError("boom")):
            run_id = c.post(f"/api/strategy-lab/strategies/{sid}/runs", json=self.run_payload()).json()["run_id"]
        self.assertEqual(c.get(f"/api/strategy-lab/runs/{run_id}").json()["status"], "failed")
        self.assertEqual(c.get(f"/api/strategy-lab/runs/{run_id}/result").status_code, 409)
        self.assertEqual(c.get(f"/api/strategy-lab/runs/{run_id}/equity").status_code, 409)


if __name__ == "__main__":
    unittest.main()
