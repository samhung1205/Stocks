"""Stage D+ research-data correctness: action classification, adjusted factor
series, universe classification and corporate-action accounting. Offline only."""
import json
import tempfile
import unittest

from sqlalchemy import create_engine

from app import corporate_actions as ca
from app.db import upsert_many, query_all, query_one
from app.db import tables as t
from app.db.migrations import migrate
from app.security_class import classify_security
from app.services import data_foundation as data
from app.services.strategy import factors
from app.services.strategy.adjustment import adjust_bars, event_ratios
from app.services.strategy.engine import run_backtest
from app.services.strategy.panel import synthetic_panel
from app.services.strategy.rules import static_signals
from app.services.strategy.types import BacktestConfig, Bar, CostConfig, SignalContext

DATES = [f"2025-01-{d:02d}" for d in range(2, 12)]
FREE = BacktestConfig(initial_cash=10_000, costs=CostConfig(0, 0, 0, 0, 0))


def raw(label, before, after, value):
    return dict(date="2025-01-06", stock_id="1101", before_price=before, after_price=after,
                stock_or_cache_dividend=label, stock_and_cache_dividend=value)


class ClassificationTests(unittest.TestCase):
    SRC = "FinMind:TaiwanStockDividendResult"

    def test_both_label_spellings_for_cash(self):
        for label in ("息", "除息"):
            c = ca.classify(self.SRC, raw(label, 2385.0, 2377.99, 7.000001))
            self.assertEqual((c["kind"], c["cash_dividend"], c["resolution"]),
                             ("dividend_cash", 7.000001, ca.CASH_VERIFIED))
            self.assertIsNone(c["share_multiplier"])
            self.assertAlmostEqual(c["ratio"], 2377.99 / 2385.0)

    def test_cash_not_filled_when_value_disagrees_with_reference_price(self):
        c = ca.classify(self.SRC, raw("除息", 100, 90, 3.0))  # gap of 10 != value 3
        self.assertIsNone(c["cash_dividend"])
        self.assertEqual(c["resolution"], ca.UNRESOLVED_VALUE)
        for bad in (None, 0, -1, "x"):
            self.assertIsNone(ca.classify(self.SRC, raw("息", 100, 97, bad))["cash_dividend"])

    def test_pure_stock_multiplier_is_unique_derivation(self):
        for label in ("權", "除權"):
            c = ca.classify(self.SRC, raw(label, 50.7, 48.28, 2.414286))
            self.assertEqual((c["kind"], c["resolution"]), ("dividend_stock", ca.STOCK_MULTIPLIER))
            self.assertAlmostEqual(c["share_multiplier"], 1.05, places=6)
            self.assertIsNone(c["cash_dividend"])
        # value <= 0 (reference above prior close) is never turned into a multiplier
        c = ca.classify(self.SRC, raw("權", 13.2, 13.4, -0.209613))
        self.assertIsNone(c["share_multiplier"])
        self.assertEqual(c["resolution"], ca.UNRESOLVED_VALUE)

    def test_mixed_and_unknown_labels_stay_unresolved(self):
        for label in ("權息", "除權息"):
            c = ca.classify(self.SRC, raw(label, 41.6, 36.91, 4.687849))
            self.assertEqual((c["kind"], c["resolution"]), ("dividend_mixed", ca.UNRESOLVED_MIXED))
            self.assertIsNone(c["cash_dividend"])
            self.assertIsNone(c["share_multiplier"])
        c = ca.classify(self.SRC, raw("其他", 10, 9, 1))
        self.assertEqual((c["kind"], c["resolution"]), ("unknown", ca.UNRESOLVED_LABEL))

    def test_split_multiplier_only_for_par_value_change(self):
        src = "FinMind:TaiwanStockSplitPrice"
        for before, after, expected in [(546.0, 136.5, 4), (109.5, 27.38, 4), (90.6, 36.24, 2.5),
                                        (885.0, 88.5, 10), (10.0, 20.0, .5)]:
            c = ca.classify(src, {"before_price": before, "after_price": after, "type": "面額變更"})
            self.assertEqual(c["resolution"], ca.SPLIT_MULTIPLIER, (before, after))
            self.assertAlmostEqual(c["share_multiplier"], expected, places=9)
        # capital reduction with unknown cash refund, or an inconsistent reference: unresolved
        for kwargs in ({"type": "減資"}, {"type": "面額變更", "after_price": 33.0}):
            row = {"before_price": 100.0, "after_price": 25.0, "type": "面額變更", **kwargs}
            c = ca.classify(src, row)
            self.assertIsNone(c["share_multiplier"], row)
            self.assertEqual(c["resolution"], ca.UNRESOLVED_SPLIT)


class RepairMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/legacy.db")

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def _row(self, sid, label, before, after, value):
        r = raw(label, before, after, value)
        r["stock_id"] = sid
        return (sid, "2025-01-06", "unknown", "FinMind:TaiwanStockDividendResult",
                before, after, after / before, json.dumps(r, ensure_ascii=False))

    def test_legacy_rows_reclassified_and_repair_is_idempotent(self):
        with self.engine.begin() as c:  # Stage A+ shape: no share_multiplier/resolution
            c.exec_driver_sql(
                "CREATE TABLE corporate_actions(stock_id TEXT,date TEXT,kind TEXT,source TEXT,"
                "before_price FLOAT,after_price FLOAT,cash_dividend FLOAT,stock_dividend FLOAT,"
                "ratio FLOAT,raw_json TEXT,PRIMARY KEY(stock_id,date,kind,source))")
            for row in (self._row("1101", "除息", 100, 97, 3.0), self._row("1102", "除權", 50.7, 48.28, 2.414286),
                        self._row("1103", "除權息", 41.6, 36.91, 4.687849)):
                c.exec_driver_sql("INSERT INTO corporate_actions(stock_id,date,kind,source,before_price,"
                                  "after_price,ratio,raw_json) VALUES(?,?,?,?,?,?,?,?)", row)
        migrate(self.engine)
        migrate(self.engine)  # rerun: ledger says v2 applied, nothing changes
        with self.engine.connect() as c:
            rows = {r["stock_id"]: r for r in query_all(c, "SELECT * FROM corporate_actions")}
            self.assertEqual(query_one(c, "SELECT COUNT(*) n FROM schema_migrations")["n"], 7)
        self.assertEqual((rows["1101"]["kind"], rows["1101"]["cash_dividend"]), ("dividend_cash", 3.0))
        self.assertEqual(rows["1102"]["kind"], "dividend_stock")
        self.assertAlmostEqual(rows["1102"]["share_multiplier"], 1.05, places=6)
        self.assertEqual((rows["1103"]["kind"], rows["1103"]["resolution"]),
                         ("dividend_mixed", ca.UNRESOLVED_MIXED))
        self.assertIsNone(rows["1103"]["cash_dividend"])
        with self.engine.begin() as c:
            stats = ca.repair(c)  # direct rerun on repaired data
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(sum(stats["resolution"].values()), 3)

    def test_repair_clears_unverified_cash(self):
        migrate(self.engine)
        with self.engine.begin() as c:
            upsert_many(c, t.corporate_actions, [dict(
                stock_id="1101", date="2025-01-06", kind="dividend_cash",
                source="FinMind:TaiwanStockDividendResult", before_price=100.0, after_price=90.0,
                cash_dividend=3.0, ratio=.9, raw_json=json.dumps(raw("息", 100, 90, 3.0)))])
            ca.repair(c)
            r = query_one(c, "SELECT * FROM corporate_actions")
        self.assertIsNone(r["cash_dividend"])  # no guessing: value does not match reference
        self.assertEqual(r["resolution"], ca.UNRESOLVED_VALUE)


def _bars(closes, sid="1101", dates=DATES):
    return {d: {sid: Bar(sid, d, open=c, close=c, amount=1e8)} for d, c in zip(dates, closes)}


def _adj(closes, actions, sid="1101"):
    out, stats = adjust_bars(_bars(closes, sid), actions, DATES[:len(closes)])
    return [out[d][sid].adj_close for d in DATES[:len(closes)]], stats


def _returns(series):
    return [b / a - 1 for a, b in zip(series, series[1:])]


class AdjustedSeriesTests(unittest.TestCase):
    def test_cash_dividend_no_artificial_gap(self):
        # ex-date 01-06: reference 97 (ratio .97), market then trades flat to slightly up
        closes = [100, 100, 97.5, 98, 99]
        adj, _ = _adj(closes, [{"stock_id": "1101", "date": DATES[2], "kind": "dividend_cash", "ratio": .97}])
        raw_ret, adj_ret = _returns(closes), _returns(adj)
        self.assertAlmostEqual(raw_ret[1], -.025)
        self.assertAlmostEqual(adj_ret[1], 97.5 / 97 - 1)  # move vs reference price only
        self.assertEqual(adj_ret[0], 0)
        for a, r in zip(adj_ret[2:], raw_ret[2:]):
            self.assertAlmostEqual(a, r)  # untouched after the event

    def test_stock_dividend_and_split_are_continuous(self):
        for kind, ratio, drop in [("dividend_stock", 1 / 1.05, 1 / 1.05), ("split", .25, .25)]:
            closes = [100, 100, 100 * drop, 100 * drop * 1.01, 100 * drop * 1.02]
            adj, _ = _adj(closes, [{"stock_id": "1101", "date": DATES[2], "kind": kind, "ratio": ratio}])
            for r in _returns(adj):
                self.assertLess(abs(r), .011, kind)  # no -5% / -75% artefact
            self.assertGreater(min(_returns(closes)), -1)
            self.assertLess(min(_returns(closes)), -.04)

    def test_point_in_time_later_events_never_rewrite_history(self):
        closes = [100, 100, 95, 95, 95, 47.5, 47.5]
        early = {"stock_id": "1101", "date": DATES[2], "kind": "dividend_cash", "ratio": .95}
        late = {"stock_id": "1101", "date": DATES[5], "kind": "split", "ratio": .5}
        with_late, _ = _adj(closes, [early, late])
        without_late, _ = _adj(closes, [early])
        self.assertEqual(with_late[:5], without_late[:5])

    def test_one_factor_per_stock_day_no_double_count(self):
        both = [{"stock_id": "1101", "date": DATES[2], "kind": "dividend_stock", "ratio": .5},
                {"stock_id": "1101", "date": DATES[2], "kind": "split", "ratio": .5}]
        ratios, stats = event_ratios(both)
        self.assertEqual(ratios, {("1101", DATES[2]): .5})
        self.assertEqual(stats["same_day_collapsed"], 1)
        adj, _ = _adj([100, 100, 50, 50], both)
        self.assertAlmostEqual(adj[2], adj[1])
        # invalid ratios are ignored, never applied
        bad, stats = event_ratios([{"stock_id": "1101", "date": DATES[2], "ratio": 0},
                                   {"stock_id": "1101", "date": DATES[3], "ratio": float("nan")},
                                   {"stock_id": "1101", "date": DATES[5], "ratio": None}])
        self.assertEqual(bad, {})

    def test_residual_jump_flags_mismatched_reference(self):
        # reference says -5% but quotes fall -50%: adjusted series keeps a visible jump + flag
        adj, stats = _adj([100, 100, 50, 50], [{"stock_id": "1101", "date": DATES[2], "ratio": .95}])
        self.assertEqual([(s, d) for s, d, _ in stats["residual_jumps"]], [("1101", DATES[2])])

    def test_missing_close_gets_no_adjusted_value(self):
        bars = {DATES[0]: {"1101": Bar("1101", DATES[0], close=100)},
                DATES[1]: {"1101": Bar("1101", DATES[1], close=None)}}
        out, _ = adjust_bars(bars, [], DATES[:2])
        self.assertEqual(out[DATES[0]]["1101"].adj_close, 100)
        self.assertIsNone(out[DATES[1]]["1101"].adj_close)

    def test_factors_use_adjusted_series_but_raw_remains_available(self):
        closes = [100.0] * 5 + [25.0] * 5  # 4:1 split, business otherwise flat
        actions = [{"stock_id": "1101", "date": DATES[5], "kind": "split", "ratio": .25, "share_multiplier": 4}]
        panel = synthetic_panel(DATES, [{"stock_id": "1101", "date": d, "open": c, "close": c, "amount": 1e8}
                                        for d, c in zip(DATES, closes)],
                                actions=actions, adjust_prices=True)
        ctx = SignalContext(DATES[-1], len(DATES) - 1, panel.days[-1].bars, panel.days, {}, {}, {}, "strict")
        self.assertAlmostEqual(factors.momentum(ctx, 8, 1)["1101"], 0.0)  # flat business
        self.assertAlmostEqual(factors.momentum(ctx, 8, 1, adjusted=False)["1101"], -.75)
        self.assertLess(factors.annualized_volatility(ctx, 8)["1101"], 1e-9)
        self.assertGreater(factors.annualized_volatility(ctx, 8, adjusted=False)["1101"], 1)
        self.assertAlmostEqual(factors.moving_average_distance(ctx, 5)["1101"], 0.0)
        self.assertEqual(panel.days[-1].bars["1101"].close, 25.0)  # execution price untouched

    def test_adjustment_never_changes_engine_accounting(self):
        closes = [100.0] * 5 + [25.0] * 5
        actions = [{"stock_id": "1101", "date": DATES[5], "kind": "split", "ratio": .25, "share_multiplier": 4}]
        bars = [{"stock_id": "1101", "date": d, "open": c, "close": c} for d, c in zip(DATES, closes)]
        sig = static_signals({DATES[0]: {"1101": 1.0}})
        plain = run_backtest(synthetic_panel(DATES, bars, actions=actions), sig, FREE)
        adjusted = run_backtest(synthetic_panel(DATES, bars, actions=actions, adjust_prices=True), sig, FREE)
        self.assertEqual(plain["equity_curve"], adjusted["equity_curve"])
        self.assertEqual(plain["fills"], adjusted["fills"])
        self.assertEqual(adjusted["equity_curve"][-1]["net_equity"], 10_000)  # 4x shares at 1/4 price


class UniverseClassificationTests(unittest.TestCase):
    CASES = [
        ("2330", "台積電", "半導體業", "stock", "common_stock", "industry"),
        ("6488", "環球晶", "半導體業", "stock", "common_stock", "industry"),
        ("2609", "陽明", "航運業", "stock", "common_stock", "industry"),
        ("1590", "亞德客-KY", "電機機械", "stock", "common_stock", "industry"),
        ("7780", "大研生醫*", "生技醫療業", "stock", "common_stock", "industry"),
        ("2887", "台新新光金", "金融保險", "stock", "common_stock", "industry"),
        ("0050", "元大台灣50", "ETF", "etf", "etf", "industry"),
        ("00679B", "元大美債20年", "上櫃指數股票型基金(ETF)", "stock", "etf", "industry"),
        ("00928", "中信上櫃ESG 30", "上櫃ETF", "stock", "etf", "industry"),
        ("00981D", "主動中信非投等債", None, "stock", "etf", "id_pattern_fallback"),
        ("02003L", "永豐富櫃200正二N", "指數投資證券(ETN)", "stock", "etn", "industry"),
        ("01001T", "土銀富邦R1", "受益證券", "stock", "reit", "industry"),
        ("9103", "美德醫療-DR", "存託憑證", "stock", "dr", "industry"),
        ("911619", "耀傑-DR", "存託憑證", "stock", "dr", "industry"),
        ("2887I", "台新新光辛特", "其他", "stock", "preferred", "name"),
        ("8916A", "光隆甲特", "其他", "stock", "preferred", "name"),
        ("4129A", None, "生技醫療業", "stock", "preferred", "id_pattern_fallback"),
        ("73107P", "原相國票9B售02", "所有證券", "stock", "warrant", "industry"),
        ("1204", "津津", None, "stock", "common_stock", "id_pattern_fallback"),
        ("A", "A", "X", "stock", "other", "industry"),
    ]

    def test_rules_and_basis(self):
        for sid, name, industry, stype, cls, basis in self.CASES:
            self.assertEqual(classify_security(sid, name, industry, stype), (cls, basis), sid)

    def test_liquid_universe_keeps_only_ordinary_shares(self):
        tmp = tempfile.TemporaryDirectory()
        engine = create_engine("sqlite:///" + tmp.name + "/u.db")
        migrate(engine)
        try:
            members = [(s, n, i) for s, n, i, ty, cls, _ in self.CASES if ty != "etf" or cls == "etf"]
            with engine.begin() as c:
                upsert_many(c, t.stocks, [dict(stock_id=s, name=n or s, industry=i, type="stock",
                                               market="tpex") for s, n, i in members])
                upsert_many(c, t.daily_quotes, [dict(stock_id=s, date=f"2025-03-{d:02d}", amount=1e9)
                                                for s, _, _ in members for d in range(1, 6)])
                got = {r["stock_id"]: r for r in data.liquid_universe(c, "2025-06-01", 30e6, 3)}
            expected = {s for s, _, _, ty, cls, _ in self.CASES if cls == "common_stock"}
            self.assertEqual(set(got), expected)
            self.assertEqual(got["1204"]["class_basis"], "id_pattern_fallback")  # flagged fallback
            self.assertEqual(got["2330"]["class_basis"], "industry")
        finally:
            engine.dispose()
            tmp.cleanup()


class ActionAccountingTests(unittest.TestCase):
    def _run(self, action, closes):
        bars = [{"stock_id": "1101", "date": d, "open": c, "close": c} for d, c in zip(DATES, closes)]
        panel = synthetic_panel(DATES, bars, actions=[action])
        return run_backtest(panel, static_signals({DATES[0]: {"1101": 1.0}}), FREE)

    def test_derived_pure_stock_multiplier_keeps_equity_continuous(self):
        c = ca.classify("FinMind:TaiwanStockDividendResult", raw("除權", 100.0, 100 / 1.05, 100 - 100 / 1.05))
        closes = [100.0] * 5 + [100 / 1.05] * 5
        result = self._run({"stock_id": "1101", "date": DATES[5], "kind": c["kind"],
                            "share_multiplier": c["share_multiplier"], "ratio": c["ratio"],
                            "resolution": c["resolution"]}, closes)
        self.assertAlmostEqual(result["equity_curve"][-1]["net_equity"], 10_000, places=6)
        self.assertEqual(result["metadata"]["unresolved_action_count"], 0)
        self.assertFalse([w for w in result["warnings"] if "UNRESOLVED" in w])

    def test_verified_cash_dividend_is_credited_once(self):
        result = self._run({"stock_id": "1101", "date": DATES[5], "kind": "dividend_cash",
                            "cash_dividend": 3.0, "ratio": .97, "resolution": ca.CASH_VERIFIED},
                           [100.0] * 5 + [97.0] * 5)
        self.assertAlmostEqual(result["equity_curve"][-1]["net_equity"], 10_000, places=6)
        self.assertEqual(result["metadata"]["unresolved_action_count"], 0)

    def test_mixed_action_is_flagged_not_guessed(self):
        result = self._run({"stock_id": "1101", "date": DATES[5], "kind": "dividend_mixed",
                            "ratio": .9, "resolution": ca.UNRESOLVED_MIXED}, [100.0] * 5 + [90.0] * 5)
        self.assertEqual(result["metadata"]["unresolved_action_count"], 1)
        item = result["metadata"]["unresolved_actions"][0]
        self.assertEqual((item["stock_id"], item["date"], item["resolution"], item["shares_held"]),
                         ("1101", DATES[5], ca.UNRESOLVED_MIXED, 100))
        self.assertTrue(any(w.startswith("UNRESOLVED_CORPORATE_ACTION") for w in result["warnings"]))
        self.assertLess(result["equity_curve"][-1]["net_equity"], 10_000)  # not silently patched

    def test_unresolved_event_on_unheld_stock_is_ignored(self):
        bars = [{"stock_id": "1101", "date": d, "open": 100, "close": 100} for d in DATES]
        panel = synthetic_panel(DATES, bars, actions=[{"stock_id": "1101", "date": DATES[5],
                                                       "kind": "dividend_mixed", "ratio": .9}])
        result = run_backtest(panel, lambda _: None, FREE)
        self.assertEqual(result["metadata"]["unresolved_action_count"], 0)

    def test_cash_kind_without_verified_amount_is_unresolved(self):
        result = self._run({"stock_id": "1101", "date": DATES[5], "kind": "dividend_cash",
                            "resolution": ca.UNRESOLVED_VALUE}, [100.0] * 10)
        self.assertEqual(result["metadata"]["unresolved_action_count"], 1)


if __name__ == "__main__":
    unittest.main()
