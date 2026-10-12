"""Deterministic synthetic coverage for all five Stage C templates."""
import datetime as dt
import unittest

from app.services.strategy import factors
from app.services.strategy.engine import run_backtest
from app.services.strategy.panel import synthetic_panel
from app.services.strategy.templates import TEMPLATES, get_template
from app.services.strategy.types import (
    BacktestConfig, CostConfig, RebalanceRule, Security, SignalContext,
)


def _dates(count=14):
    start = dt.date(2025, 1, 2)
    return [(start + dt.timedelta(days=index)).isoformat() for index in range(count)]


def _fixture(*, publication_mode="strict", extra_publications=(), extra_dates=()):
    dates = _dates() + list(extra_dates)
    paths = {
        "A": [10 + index for index in range(len(dates))],
        "B": [10 + index * .5 for index in range(len(dates))],
        "C": [10 for _ in dates],
        "D": [20 - index * .4 for index in range(len(dates))],
        "E": [10 + (1 if index % 2 else -1) for index in range(len(dates))],
    }
    bars = [{"stock_id": stock_id, "date": date, "open": closes[index],
             "close": closes[index], "amount": 100_000_000}
            for stock_id, closes in paths.items()
            for index, date in enumerate(dates)]
    securities = [{"stock_id": stock_id, "security_type": "stock",
                   "industry": "Tech" if stock_id in "ABC" else "Finance",
                   "industry_source": "current_stocks"}
                  for stock_id in paths]
    publications = []
    profiles = {
        "A": (10, 12, .50, .20), "B": (8, 9, .40, .30),
        "C": (3, 3, .20, .60), "D": (-1, 0, .10, .80), "E": (2, 1, .15, .70),
    }
    periods = [f"{year}Q{quarter}" for year in (2023, 2024) for quarter in range(1, 5)]
    for stock_id, (income, cashflow, margin, debt) in profiles.items():
        for index, period in enumerate(periods):
            publications.append({
                "dataset": "financials", "stock_id": stock_id,
                "publish_date": "2025-01-01", "publish_source": "actual",
                "period": period,
                "values": {
                    "revenue": 100 + index * (12 if stock_id == "A" else 4),
                    "gross_profit": (100 + index * (12 if stock_id == "A" else 4)) * margin,
                    "net_income": income, "op_cashflow": cashflow, "equity": 100,
                    "total_assets": 100, "total_liabilities": debt * 100,
                },
            })
    yoy = {
        "A": [5, 6, 10, 16], "B": [2, 3, 4, 7], "C": [8, 7, 6, 5],
        "D": [-5, -3, -1, 0], "E": [1, 1, 1, 1],
    }
    for stock_id, values in yoy.items():
        for month, value in enumerate(values, 1):
            publications.append({
                "dataset": "month_revenue", "stock_id": stock_id,
                "publish_date": "2025-01-01", "publish_source": "actual",
                "period": f"2024-{month:02d}",
                "values": {"revenue": 100 + month, "yoy": value},
            })
    publications.extend(extra_publications)
    valuations = [
        {"stock_id": "A", "date": dates[0], "pe": 5, "pb": .8},
        {"stock_id": "B", "date": dates[0], "pe": 8, "pb": 1.0},
        {"stock_id": "C", "date": dates[0], "pe": 20, "pb": 2.0},
        {"stock_id": "D", "date": dates[0], "pe": 30, "pb": 3.0},
        {"stock_id": "E", "date": dates[0], "pe": -2, "pb": None},
    ]
    return synthetic_panel(dates, bars, securities=securities,
                           publications=publications, valuations=valuations,
                           publication_mode=publication_mode)


def _overrides(top_n=1):
    return {
        "universe": {"liquidity_lookback": 5, "min_liquidity_observations": 5,
                     "min_average_daily_amount": 1},
        "ranking": {"winsor_lower": 0, "winsor_upper": 1,
                    "missing_policy": "exclude"},
        "selection": {"top_n": top_n},
        "momentum": {"lookback": 5, "skip_recent": 1},
        "trend": {"ma_lookback": 3, "require_above_ma": True},
        "volatility": {"lookback": 4},
        "revenue": {"acceleration_window": 2},
        "value": {"max_valuation_age_days": 365, "minimum_sector_size": 2},
    }


def _capture(panel, rule):
    observed = {}

    def callback(context):
        signal = rule(context)
        observed[context.date] = signal
        return signal

    run_backtest(panel, callback, BacktestConfig(
        initial_cash=100_000, costs=CostConfig(0, 0, 0, 0, 0),
        rebalance=RebalanceRule("daily")))
    return observed


class StrategyTemplateTests(unittest.TestCase):
    def test_rank_ties_missing_and_winsorization(self):
        values = {"A": 1, "B": 1, "C": 3, "D": None}
        ranked = factors.percentile_rank(values)
        self.assertEqual(ranked, {"A": .25, "B": .25, "C": 1.0, "D": None})
        self.assertEqual(factors.percentile_rank(values, missing="median")["D"], .5)
        self.assertEqual(factors.percentile_rank(values, missing="worst")["D"], 0)
        self.assertEqual(factors.winsorize({"A": 0, "B": 10, "C": 100}, .25, .75),
                         {"A": 5.0, "B": 10.0, "C": 55.0})

    def test_quality_growth_momentum_template_is_deterministic(self):
        override = _overrides()
        override["factor_weights"] = {"quality": 1, "growth": 0, "momentum": 0}
        rule = get_template("quality_growth_momentum").signal_rule(override)
        observed = _capture(_fixture(), rule)
        signal = observed[_dates()[-2]]
        self.assertEqual(signal.candidates, ("A",))
        self.assertEqual(signal, _capture(_fixture(), rule.clone())[_dates()[-2]])

    def test_revenue_growth_acceleration_template_and_publication_mode(self):
        rule = get_template("revenue_growth_acceleration").signal_rule(_overrides())
        signal = _capture(_fixture(), rule)[_dates()[-2]]
        self.assertEqual(signal.candidates, ("A",))

        assumed_rows = [{
            "dataset": "month_revenue", "stock_id": "A", "publish_date": "2025-01-01",
            "publish_source": "assumed_deadline", "period": f"2023-{month:02d}",
            "values": {"revenue": 100, "yoy": value},
        } for month, value in enumerate((1, 2, 4, 8), 1)]
        # A panel is the enforcement boundary: strict drops assumed rows, assumed exposes them.
        bare_dates = _dates(8)
        bars = [{"stock_id": "A", "date": date, "open": 10, "close": 10,
                 "amount": 100_000_000} for date in bare_dates]
        securities = [{"stock_id": "A", "security_type": "stock"}]
        strict = synthetic_panel(bare_dates, bars, securities=securities,
                                 publications=assumed_rows, publication_mode="strict")
        assumed = synthetic_panel(bare_dates, bars, securities=securities,
                                  publications=assumed_rows, publication_mode="assumed")
        small = _overrides()
        small["universe"].update(liquidity_lookback=3, min_liquidity_observations=3)
        strict_signal = _capture(strict, get_template("revenue_growth_acceleration").signal_rule(small))[bare_dates[-2]]
        assumed_signal = _capture(assumed, get_template("revenue_growth_acceleration").signal_rule(small))[bare_dates[-2]]
        self.assertEqual(strict_signal.candidates, ())
        self.assertEqual(assumed_signal.candidates, ("A",))
        self.assertTrue(any("assumed publication mode" in text for text in assumed_signal.warnings))

    def test_momentum_trend_template_and_config_clone(self):
        override = _overrides()
        override["factor_weights"] = {"momentum": 1, "trend": 0}
        template = get_template("momentum_trend")
        one = _capture(_fixture(), template.signal_rule(override))[_dates()[-2]]
        two_override = {**override, "selection": {"top_n": 2}}
        two = _capture(_fixture(), template.signal_rule(two_override))[_dates()[-2]]
        self.assertEqual(one.candidates, ("A",))
        self.assertEqual(two.candidates, ("A", "B"))
        self.assertEqual(template.default_config["selection"]["top_n"], 30)

    def test_value_quality_sector_neutral_and_explicit_fallback(self):
        override = _overrides()
        override["factor_weights"] = {"value": 1, "quality": 0}
        signal = _capture(_fixture(), get_template("value_quality").signal_rule(override))[_dates()[-2]]
        self.assertEqual(signal.candidates, ("A",))
        self.assertTrue(any("sector-neutral fallback" in text for text in signal.warnings))

        context = SignalContext(
            date="2025-01-10", index=0, bars={}, history=(), fundamentals={},
            securities={
                "A": Security("A", industry="Tech", industry_source="point_in_time"),
                "B": Security("B", industry="Finance", industry_source="point_in_time"),
                "C": Security("C", industry="Tech", industry_source="point_in_time"),
                "D": Security("D", industry="Finance", industry_source="point_in_time"),
            })
        ranks, warnings = factors.sector_neutral_rank(
            {"A": 1, "B": 2, "C": 3, "D": 4}, context,
            minimum_group_size=2, winsor_limits=(0, 1))
        self.assertEqual((ranks["A"], ranks["B"], ranks["C"], ranks["D"]), (0, 0, 1, 1))
        self.assertEqual(warnings, ())

    def test_low_volatility_quality_keeps_liquidity_filter(self):
        override = _overrides()
        override["factor_weights"] = {"low_volatility": 1, "quality": 0}
        signal = _capture(_fixture(), get_template("low_volatility_quality").signal_rule(override))[_dates()[-2]]
        self.assertEqual(signal.candidates, ("C",))
        strict_liquidity = {**override, "universe": {
            **override["universe"], "min_average_daily_amount": 200_000_000}}
        excluded = _capture(_fixture(), get_template("low_volatility_quality").signal_rule(strict_liquidity))[_dates()[-2]]
        self.assertEqual(excluded.candidates, ())

    def test_future_data_cannot_change_past_signal(self):
        signal_date = _dates()[-2]
        baseline = _capture(
            _fixture(), get_template("revenue_growth_acceleration").signal_rule(_overrides()))[signal_date]
        future_date = "2025-02-01"
        future = [{
            "dataset": "month_revenue", "stock_id": "E", "publish_date": future_date,
            "publish_source": "actual", "period": "2025-01",
            "values": {"revenue": 999999, "yoy": 9999},
        }]
        extended = _capture(
            _fixture(extra_publications=future, extra_dates=(future_date, "2025-02-02")),
            get_template("revenue_growth_acceleration").signal_rule(_overrides()))[signal_date]
        self.assertEqual(baseline.targets, extended.targets)
        self.assertEqual(baseline.scores, extended.scores)

    def test_template_metadata_contract(self):
        self.assertEqual(len(TEMPLATES), 5)
        for template in TEMPLATES.values():
            metadata = template.metadata()
            self.assertTrue(metadata["hypothesis"])
            self.assertTrue(metadata["required_data"])
            self.assertTrue(metadata["known_failure_regimes"])
            self.assertIn("rebalance", metadata["default_config"])


if __name__ == "__main__":
    unittest.main()
