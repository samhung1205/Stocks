"""Cloneable Stage C Strategy Template definitions.

Defaults are research starting points, not optimized parameters or performance
claims.  ``default_config`` is copied before every use so callers can modify a
clone without mutating the shared template catalog.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Mapping

from .signals import TemplateSignalRule
from .types import BacktestConfig, RebalanceRule


def _common(*, top_n=30, rebalance="monthly") -> dict[str, Any]:
    return {
        "universe": {
            "liquidity_lookback": 120,
            "min_liquidity_observations": 120,
            "min_average_daily_amount": 30_000_000,
            "security_types": ["stock"],
        },
        "ranking": {
            "winsor_lower": .05,
            "winsor_upper": .95,
            "missing_policy": "exclude",
        },
        "selection": {"top_n": top_n},
        "portfolio": {"weighting": "equal", "cash_weight": 0.0},
        "rebalance": {"frequency": rebalance, "dates": []},
    }


def _quality_weights() -> dict[str, float]:
    return {"roe": .35, "cash_conversion": .25, "gross_margin": .20, "low_debt": .20}


@dataclass(frozen=True)
class StrategyTemplate:
    key: str
    name: str
    hypothesis: str
    required_data: Mapping[str, Any]
    known_failure_regimes: tuple[str, ...]
    default_config: Mapping[str, Any]

    def clone_config(self, overrides: Mapping | None = None) -> dict[str, Any]:
        from .signals import _deep_merge
        return _deep_merge(self.default_config, overrides)

    def signal_rule(self, overrides: Mapping | None = None) -> TemplateSignalRule:
        return TemplateSignalRule(self.key, self.default_config, overrides)

    def backtest_config(self, overrides: Mapping | None = None,
                        base: BacktestConfig | None = None) -> BacktestConfig:
        """Apply the clone's rebalance setting to the existing Stage B config."""
        config = self.clone_config(overrides)
        schedule = config["rebalance"]
        rule = RebalanceRule(str(schedule["frequency"]),
                             frozenset(str(value) for value in schedule.get("dates", ())))
        return replace(base or BacktestConfig(), rebalance=rule)

    def metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "hypothesis": self.hypothesis,
            "required_data": copy.deepcopy(dict(self.required_data)),
            "known_failure_regimes": list(self.known_failure_regimes),
            "default_config": self.clone_config(),
        }


qgm = _common(top_n=30)
qgm.update({
    "quality_weights": _quality_weights(),
    "growth": {"weights": {"revenue_yoy": .50, "financial_growth": .50}},
    "momentum": {"lookback": 252, "skip_recent": 21},
    "factor_weights": {"quality": .40, "growth": .30, "momentum": .30},
})

revenue_acceleration = _common(top_n=30)
revenue_acceleration.update({
    "revenue": {
        "acceleration_window": 3,
        "weights": {"yoy": .40, "acceleration": .60},
        "require_positive_yoy": True,
        "require_positive_acceleration": True,
    },
})

momentum_trend = _common(top_n=30)
momentum_trend.update({
    "momentum": {"lookback": 252, "skip_recent": 21},
    "trend": {"ma_lookback": 200, "require_above_ma": True},
    "factor_weights": {"momentum": .70, "trend": .30},
})

value_quality = _common(top_n=30)
value_quality.update({
    "quality_weights": _quality_weights(),
    "value": {
        "weights": {"earnings_yield": .60, "book_yield": .40},
        "max_valuation_age_days": 10,
        "sector_neutral": True,
        "minimum_sector_size": 3,
        "sector_fallback": "global_with_warning",
    },
    "factor_weights": {"value": .55, "quality": .45},
})

low_volatility_quality = _common(top_n=30)
low_volatility_quality.update({
    "quality_weights": _quality_weights(),
    "volatility": {"lookback": 126},
    "factor_weights": {"low_volatility": .60, "quality": .40},
})


TEMPLATES: dict[str, StrategyTemplate] = {
    "quality_growth_momentum": StrategyTemplate(
        key="quality_growth_momentum",
        name="Quality × Growth × Momentum",
        hypothesis=("Companies with profitable balance-sheet quality and visible growth should "
                    "outperform more consistently when price momentum confirms the thesis."),
        required_data={
            "prices": "252+ trading sessions through signal date",
            "month_revenue": "publication-dated monthly revenue",
            "financials": "8 visible quarters for TTM growth and 4 for quality",
            "publication_modes": {
                "strict": "requires actual/observed publications; current financial coverage may be sparse",
                "assumed": "permits assumed_deadline and must remain labelled assumed",
            },
        },
        known_failure_regimes=("sharp factor rotations", "early-cycle low-quality rallies",
                               "crowded momentum reversals", "sparse strict-mode financial coverage"),
        default_config=qgm,
    ),
    "revenue_growth_acceleration": StrategyTemplate(
        key="revenue_growth_acceleration",
        name="Revenue Growth Acceleration",
        hypothesis=("Positive and accelerating monthly revenue growth may identify improving "
                    "business momentum before it is fully reflected in quarterly reports."),
        required_data={
            "month_revenue": "at least 6 visible YoY observations (or 18 months to derive them)",
            "availability": "a revenue row is usable only after its publish_date",
            "publication_modes": {
                "strict": "actual/observed_finmind only",
                "assumed": "also accepts assumed_deadline with an explicit warning",
            },
        },
        known_failure_regimes=("base-effect distortions", "cyclical peaks", "one-off revenue recognition",
                               "delayed or restated revenue data"),
        default_config=revenue_acceleration,
    ),
    "momentum_trend": StrategyTemplate(
        key="momentum_trend",
        name="Momentum + Trend Following",
        hypothesis=("Medium-term relative strength that remains above a long moving average is "
                    "more likely to persist than unconfirmed momentum."),
        required_data={"prices": "253 trading sessions for 12-1 momentum and 200-day MA",
                       "fundamentals": "none"},
        known_failure_regimes=("range-bound whipsaws", "gap reversals", "fast crash-and-rebound markets",
                               "unadjusted corporate-action discontinuities"),
        default_config=momentum_trend,
    ),
    "value_quality": StrategyTemplate(
        key="value_quality",
        name="Value × Quality",
        hypothesis=("Inexpensive securities with durable profitability and balance-sheet quality "
                    "may avoid part of the value-trap problem."),
        required_data={
            "valuations": "dated positive PE/PB observations supplied by panel.py",
            "financials": "4 visible quarters for quality",
            "sector": ("historical point-in-time sector metadata for true sector-neutral ranks; "
                       "current stocks.industry is rejected and falls back globally"),
            "publication_modes": {
                "strict": "quality requires actual/observed publications",
                "assumed": "permits assumed_deadline and must remain labelled assumed",
            },
        },
        known_failure_regimes=("value traps", "structural industry decline", "rate-driven multiple changes",
                               "thin valuation history", "missing historical sector taxonomy"),
        default_config=value_quality,
    ),
    "low_volatility_quality": StrategyTemplate(
        key="low_volatility_quality",
        name="Low Volatility × Quality",
        hypothesis=("Lower realized volatility combined with fundamental quality may provide a "
                    "more defensive equity basket without selecting merely stale illiquid prices."),
        required_data={
            "prices": "127 sessions for 126-day realized volatility",
            "liquidity": "120 valid amount observations and NT$30m average daily amount by default",
            "financials": "4 visible quarters for quality",
            "publication_modes": {
                "strict": "quality requires actual/observed publications",
                "assumed": "permits assumed_deadline and must remain labelled assumed",
            },
        },
        known_failure_regimes=("sharp risk-on rallies", "volatility regime breaks", "crowded defensive trades",
                               "stale prices when liquidity filters are relaxed"),
        default_config=low_volatility_quality,
    ),
}


def get_template(key: str) -> StrategyTemplate:
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        raise KeyError(f"unknown strategy template: {key}") from exc


__all__ = ["StrategyTemplate", "TEMPLATES", "get_template"]
