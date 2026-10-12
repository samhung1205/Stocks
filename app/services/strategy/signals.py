"""Composable Stage C signal rules that emit Stage B target portfolios."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from . import factors
from .types import Signal, SignalContext


def _deep_merge(base: Mapping, overrides: Mapping | None) -> dict:
    output = copy.deepcopy(dict(base))
    for key, value in (overrides or {}).items():
        if isinstance(value, Mapping) and isinstance(output.get(key), Mapping):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = copy.deepcopy(value)
    return output


def _common(config: Mapping) -> tuple[set[str], dict, tuple[float, float], str]:
    universe = config["universe"]
    eligible = factors.investable_universe(
        config["context"],
        lookback=int(universe["liquidity_lookback"]),
        min_observations=int(universe["min_liquidity_observations"]),
        min_average_amount=float(universe["min_average_daily_amount"]),
        security_types=tuple(universe.get("security_types", ("stock",))),
    )
    ranking = config["ranking"]
    limits = (float(ranking["winsor_lower"]), float(ranking["winsor_upper"]))
    missing = str(ranking["missing_policy"])
    if missing not in ("exclude", "median", "worst"):
        raise ValueError("ranking.missing_policy must be exclude, median, or worst")
    return eligible, ranking, limits, missing


def _only(values: Mapping[str, float | None], eligible: set[str]) -> dict[str, float | None]:
    return {stock_id: values.get(stock_id) for stock_id in eligible}


def _rank(values: Mapping[str, float | None], eligible: set[str], limits,
          missing: str, *, higher_is_better=True) -> dict[str, float | None]:
    return factors.ranked_factor(_only(values, eligible),
                                 higher_is_better=higher_is_better,
                                 winsor_limits=limits, missing=missing)


def _quality(context: SignalContext, eligible: set[str], config: Mapping,
             limits, missing) -> tuple[dict[str, float], dict]:
    raw = factors.financial_quality(context)
    ranks = {name: _rank(values, eligible, limits, missing)
             for name, values in raw.items()}
    scores = factors.combine_ranked(ranks, config["quality_weights"], missing=missing)
    return scores, {"raw": raw, "ranks": ranks}


def _targets(scores: Mapping[str, float], config: Mapping) -> tuple[dict[str, float], tuple[str, ...]]:
    top_n = int(config["selection"]["top_n"])
    if top_n < 1:
        raise ValueError("selection.top_n must be positive")
    selected = tuple(sorted(scores, key=lambda stock_id: (-scores[stock_id], stock_id))[:top_n])
    if not selected:
        return {}, ()
    portfolio = config["portfolio"]
    investable = 1 - float(portfolio.get("cash_weight", 0))
    if not 0 < investable <= 1:
        raise ValueError("portfolio.cash_weight must be in [0, 1)")
    weighting = portfolio.get("weighting", "equal")
    if weighting == "equal":
        return {stock_id: investable / len(selected) for stock_id in selected}, selected
    if weighting == "score":
        positive = {stock_id: max(float(scores[stock_id]), 0.0) for stock_id in selected}
        total = sum(positive.values())
        if total <= 0:
            return {stock_id: investable / len(selected) for stock_id in selected}, selected
        return {stock_id: investable * positive[stock_id] / total for stock_id in selected}, selected
    raise ValueError("portfolio.weighting must be equal or score")


def _fundamental_warning(context: SignalContext) -> tuple[str, ...]:
    return (("assumed publication mode: assumed_deadline fundamentals may not match actual "
             "issuer announcement dates"),) if context.publication_mode == "assumed" else ()


class TemplateSignalRule:
    """Callable strategy rule with cloneable config and last-run diagnostics."""

    def __init__(self, template_key: str, default_config: Mapping,
                 overrides: Mapping | None = None):
        self.template_key = template_key
        self.config = _deep_merge(default_config, overrides)
        self.last_signal: Signal | None = None

    def clone(self, overrides: Mapping | None = None) -> "TemplateSignalRule":
        return TemplateSignalRule(self.template_key, self.config, overrides)

    def __call__(self, context: SignalContext) -> Signal:
        runtime = {**self.config, "context": context}
        eligible, _, limits, missing = _common(runtime)
        warnings: list[str] = []
        diagnostics: dict = {
            "template": self.template_key,
            "as_of": context.date,
            "publication_mode": context.publication_mode,
            "eligible_count": len(eligible),
        }

        if self.template_key == "quality_growth_momentum":
            quality, quality_detail = _quality(context, eligible, self.config, limits, missing)
            growth_cfg = self.config["growth"]
            growth_ranks = {
                "revenue_yoy": _rank(factors.revenue_yoy(context), eligible, limits, missing),
                "financial_growth": _rank(factors.financial_growth(context), eligible, limits, missing),
            }
            growth = factors.combine_ranked(
                growth_ranks, growth_cfg["weights"], missing=missing)
            momentum_raw = factors.momentum(
                context, int(self.config["momentum"]["lookback"]),
                int(self.config["momentum"]["skip_recent"]))
            momentum_rank = _rank(momentum_raw, eligible, limits, missing)
            scores = factors.combine_ranked(
                {"quality": quality, "growth": growth, "momentum": momentum_rank},
                self.config["factor_weights"], missing=missing)
            diagnostics.update(quality=quality_detail, growth=growth_ranks,
                               momentum=momentum_raw)
            warnings.extend(_fundamental_warning(context))

        elif self.template_key == "revenue_growth_acceleration":
            revenue_cfg = self.config["revenue"]
            yoy = _only(factors.revenue_yoy(context), eligible)
            acceleration = _only(factors.revenue_acceleration(
                context, int(revenue_cfg["acceleration_window"])), eligible)
            yoy_rank = factors.ranked_factor(yoy, winsor_limits=limits, missing=missing)
            acceleration_rank = factors.ranked_factor(
                acceleration, winsor_limits=limits, missing=missing)
            scores = factors.combine_ranked(
                {"yoy": yoy_rank, "acceleration": acceleration_rank},
                revenue_cfg["weights"], missing=missing)
            if revenue_cfg.get("require_positive_yoy", True):
                scores = {stock_id: score for stock_id, score in scores.items()
                          if yoy.get(stock_id) is not None and yoy[stock_id] > 0}
            if revenue_cfg.get("require_positive_acceleration", True):
                scores = {stock_id: score for stock_id, score in scores.items()
                          if acceleration.get(stock_id) is not None and acceleration[stock_id] > 0}
            diagnostics.update(revenue_yoy=yoy, revenue_acceleration=acceleration)
            warnings.extend(_fundamental_warning(context))

        elif self.template_key == "momentum_trend":
            momentum_cfg = self.config["momentum"]
            trend_cfg = self.config["trend"]
            raw_momentum = _only(factors.momentum(
                context, int(momentum_cfg["lookback"]),
                int(momentum_cfg["skip_recent"])), eligible)
            raw_trend = _only(factors.moving_average_distance(
                context, int(trend_cfg["ma_lookback"])), eligible)
            candidate_ids = eligible
            if trend_cfg.get("require_above_ma", True):
                candidate_ids = {stock_id for stock_id in eligible
                                 if raw_trend.get(stock_id) is not None and raw_trend[stock_id] > 0}
            scores = factors.combine_ranked(
                {"momentum": _rank(raw_momentum, candidate_ids, limits, missing),
                 "trend": _rank(raw_trend, candidate_ids, limits, missing)},
                self.config["factor_weights"], missing=missing)
            diagnostics.update(momentum=raw_momentum, trend_distance=raw_trend)

        elif self.template_key == "value_quality":
            value_cfg = self.config["value"]
            valuation = factors.valuation_factors(
                context, int(value_cfg["max_valuation_age_days"]))
            raw_factors = {
                "earnings_yield": _only(valuation["earnings_yield"], eligible),
                "book_yield": _only(valuation["book_yield"], eligible),
            }
            value_ranks = {}
            for name, values in raw_factors.items():
                if value_cfg.get("sector_neutral", True):
                    rank, sector_warnings = factors.sector_neutral_rank(
                        values, context,
                        minimum_group_size=int(value_cfg["minimum_sector_size"]),
                        missing=missing, winsor_limits=limits)
                    warnings.extend(sector_warnings)
                    value_ranks[name] = rank
                else:
                    value_ranks[name] = factors.ranked_factor(
                        values, winsor_limits=limits, missing=missing)
            value = factors.combine_ranked(value_ranks, value_cfg["weights"], missing=missing)
            quality, quality_detail = _quality(context, eligible, self.config, limits, missing)
            scores = factors.combine_ranked(
                {"value": value, "quality": quality},
                self.config["factor_weights"], missing=missing)
            diagnostics.update(valuation=raw_factors, value_ranks=value_ranks,
                               quality=quality_detail)
            warnings.extend(_fundamental_warning(context))

        elif self.template_key == "low_volatility_quality":
            vol_cfg = self.config["volatility"]
            raw_vol = factors.annualized_volatility(
                context, int(vol_cfg["lookback"]))
            vol_rank = _rank(raw_vol, eligible, limits, missing, higher_is_better=False)
            quality, quality_detail = _quality(context, eligible, self.config, limits, missing)
            scores = factors.combine_ranked(
                {"low_volatility": vol_rank, "quality": quality},
                self.config["factor_weights"], missing=missing)
            diagnostics.update(volatility=raw_vol, quality=quality_detail,
                               liquidity_filter=self.config["universe"])
            warnings.extend(_fundamental_warning(context))

        else:
            raise KeyError(f"unknown strategy template: {self.template_key}")

        scores = {stock_id: score for stock_id, score in scores.items()
                  if stock_id in eligible and math.isfinite(float(score))}
        targets, selected = _targets(scores, self.config)
        diagnostics["selected"] = selected
        signal = Signal(targets=targets, candidates=selected, scores=scores,
                        warnings=tuple(dict.fromkeys(warnings)), diagnostics=diagnostics)
        self.last_signal = signal
        return signal


__all__ = ["TemplateSignalRule"]
