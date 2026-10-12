"""Offline unit coverage for the Stage B backtest boundary and engine."""
import tempfile
import unittest

from sqlalchemy import create_engine

from app.db import upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services.strategy.engine import run_backtest
from app.services.strategy.metrics import trade_statistics
from app.services.strategy.panel import build_panel, synthetic_panel
from app.services.strategy.rules import static_signals
from app.services.strategy.types import (
    BacktestConfig, Bar, CostConfig, ExecutionRule, Panel, PanelDay,
    PositionSizingRule, Security,
)


class BacktestCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.tmp.name + "/strategy.db")
        migrate(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_db_panel_publication_boundary_and_mode(self):
        dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        with self.engine.begin() as conn:
            upsert_many(conn, t.stocks, [{"stock_id": "A", "name": "A", "type": "stock",
                                          "industry": "Semiconductor"}])
            upsert_many(conn, t.trading_calendar, [{"date": d} for d in dates])
            upsert_many(conn, t.daily_quotes, [
                {"stock_id": "A", "date": d, "open": 10, "close": 10} for d in dates])
            upsert_many(conn, t.month_revenue, [
                {"stock_id": "A", "ym": "2024-12", "revenue": 1,
                 "publish_date": "2025-01-03", "publish_source": "actual"}])
            upsert_many(conn, t.financials, [
                {"stock_id": "A", "quarter": "2024Q4", "eps": 1,
                 "publish_date": "2025-01-03", "publish_source": "assumed_deadline"}])
            upsert_many(conn, t.valuations, [
                {"stock_id": "A", "date": "2025-01-02", "pe": 10, "pb": 2},
                {"stock_id": "A", "date": "2025-01-06", "pe": 12, "pb": 2.2},
            ])
        strict = build_panel(dates[0], dates[-1], ["A"], publication_mode="strict",
                             db_engine=self.engine, benchmark_id=None)
        self.assertEqual(strict.days[1].publications, ())
        self.assertEqual([p.dataset for p in strict.days[2].publications], ["month_revenue"])
        self.assertEqual(strict.days[0].valuations["A"].pe, 10)
        self.assertEqual(strict.days[1].valuations, {})
        self.assertEqual(strict.securities["A"].industry_source, "current_stocks")
        assumed = build_panel(dates[0], dates[-1], ["A"], publication_mode="assumed",
                              db_engine=self.engine, benchmark_id=None)
        self.assertEqual({p.dataset for p in assumed.days[2].publications},
                         {"month_revenue", "financials"})

    def test_non_trading_event_day_does_not_count_as_execution_delay(self):
        panel = Panel((
            PanelDay("2025-01-02", {"A": Bar("A", "2025-01-02", open=10, close=10)}),
            PanelDay("2025-01-04", {}, is_trading_session=False),
            PanelDay("2025-01-06", {"A": Bar("A", "2025-01-06", open=10, close=10)}),
        ), {"A": Security("A")}, "strict")
        result = run_backtest(panel, static_signals({"2025-01-02": {"A": .5}}),
                              BacktestConfig(initial_cash=1000,
                                  execution=ExecutionRule("open", 1),
                                  costs=CostConfig(0, 0, 0, 0, 0)))
        self.assertEqual(result["fills"][0]["date"], "2025-01-06")

    def test_position_sizing_and_zero_trade_metrics(self):
        panel = synthetic_panel(["2025-01-02", "2025-01-03"], [
            {"stock_id": sid, "date": date, "open": 10, "close": 10}
            for date in ("2025-01-02", "2025-01-03") for sid in ("A", "B")])
        config = BacktestConfig(initial_cash=1000, costs=CostConfig(0, 0, 0, 0, 0),
                                sizing=PositionSizingRule("equal", .4, .2, 1))
        result = run_backtest(panel, static_signals({"2025-01-02": ["A", "B"]}), config)
        self.assertEqual([f["shares"] for f in result["fills"]], [40, 40])
        zero = run_backtest(panel, lambda _: None, config)
        self.assertEqual(zero["metrics"]["number_of_trades"], 0)
        self.assertEqual(zero["metrics"]["net_return"], 0)

    def test_payoff_ratio_and_standard_profit_factor_edges(self):
        def stats(*pnls):
            return trade_statistics([{"net_pnl": pnl} for pnl in pnls])

        mixed = stats(100, 50, -25, -75, 0)
        # Legacy journal compatibility includes breakeven trades in losses when
        # calculating average_loss: avg_win=75, avg_loss=100/3.
        self.assertAlmostEqual(mixed["payoff_ratio"], 2.25)
        self.assertAlmostEqual(mixed["profit_factor"], 1.5)
        self.assertIsNone(stats()["payoff_ratio"])
        self.assertIsNone(stats()["profit_factor"])
        self.assertIsNone(stats(10, 20)["payoff_ratio"])
        self.assertIsNone(stats(10, 20)["profit_factor"])
        self.assertEqual(stats(-10, -30)["payoff_ratio"], 0)
        self.assertEqual(stats(-10, -30)["profit_factor"], 0)
        self.assertIsNone(stats(0)["payoff_ratio"])
        self.assertIsNone(stats(0)["profit_factor"])


if __name__ == "__main__":
    unittest.main()
