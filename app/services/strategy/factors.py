"""Point-in-time factor calculations for Strategy Templates.

Every function consumes :class:`SignalContext` (or values already derived from
it).  This module deliberately has no database imports: publication filtering,
valuation dates, and price-history truncation remain the responsibility of
``panel.py`` and the Stage B engine contract.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from .types import SignalContext

MissingPolicy = Literal["exclude", "median", "worst"]


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires at least one value")
    probability = min(1.0, max(0.0, float(probability)))
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = probability * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return sorted_values[low] * (1 - fraction) + sorted_values[high] * fraction


def winsorize(values: Mapping[str, float | None], lower: float = .05,
              upper: float = .95) -> dict[str, float | None]:
    """Clamp finite observations to inclusive cross-sectional quantiles."""
    if not 0 <= lower <= upper <= 1:
        raise ValueError("winsor limits must satisfy 0 <= lower <= upper <= 1")
    valid = sorted(value for raw in values.values()
                   if (value := _number(raw)) is not None)
    if not valid:
        return {str(key): None for key in values}
    floor, ceiling = _quantile(valid, lower), _quantile(valid, upper)
    return {str(key): (None if (value := _number(raw)) is None
                       else min(ceiling, max(floor, value)))
            for key, raw in values.items()}


def percentile_rank(values: Mapping[str, float | None], *,
                    higher_is_better: bool = True,
                    missing: MissingPolicy = "exclude") -> dict[str, float | None]:
    """Return deterministic [0, 1] percentile ranks with average-rank ties.

    A one-name or all-tied cross-section receives 0.5.  Missing observations
    are excluded, neutral-filled at 0.5, or assigned the worst score at 0.0.
    Security identifiers never break a numeric tie; they only make later Top-N
    selection deterministic.
    """
    if missing not in ("exclude", "median", "worst"):
        raise ValueError("missing must be exclude, median, or worst")
    valid = [(str(key), value) for key, raw in values.items()
             if (value := _number(raw)) is not None]
    valid.sort(key=lambda item: item[1])
    result: dict[str, float | None] = {}
    index = 0
    while index < len(valid):
        end = index + 1
        while end < len(valid) and valid[end][1] == valid[index][1]:
            end += 1
        if len(valid) == 1 or (index == 0 and end == len(valid)):
            rank = .5
        else:
            rank = ((index + end - 1) / 2) / (len(valid) - 1)
        if not higher_is_better:
            rank = 1 - rank
        for key, _ in valid[index:end]:
            result[key] = rank
        index = end
    fill = None if missing == "exclude" else (.5 if missing == "median" else 0.0)
    for key in values:
        result.setdefault(str(key), fill)
    return result


def ranked_factor(values: Mapping[str, float | None], *,
                  higher_is_better: bool = True,
                  winsor_limits: tuple[float, float] = (.05, .95),
                  missing: MissingPolicy = "exclude") -> dict[str, float | None]:
    return percentile_rank(winsorize(values, *winsor_limits),
                           higher_is_better=higher_is_better, missing=missing)


def combine_ranked(factors: Mapping[str, Mapping[str, float | None]],
                   weights: Mapping[str, float], *,
                   missing: MissingPolicy = "exclude") -> dict[str, float]:
    """Combine already-oriented ranks using configurable missing treatment."""
    if missing not in ("exclude", "median", "worst"):
        raise ValueError("missing must be exclude, median, or worst")
    active = {name: float(weight) for name, weight in weights.items()
              if float(weight) > 0 and name in factors}
    if not active:
        raise ValueError("at least one positive factor weight is required")
    ids = set().union(*(values.keys() for values in factors.values()))
    output = {}
    for stock_id in ids:
        numerator = denominator = 0.0
        unavailable = False
        for name, weight in active.items():
            value = _number(factors[name].get(stock_id))
            if value is None:
                if missing == "exclude":
                    unavailable = True
                    break
                value = .5 if missing == "median" else 0.0
            numerator += value * weight
            denominator += weight
        if not unavailable and denominator:
            output[str(stock_id)] = numerator / denominator
    return output


def _bars_by_stock(context: SignalContext) -> dict[str, list]:
    output = defaultdict(list)
    for day in context.history:
        for stock_id, bar in day.bars.items():
            output[stock_id].append(bar)
    return output


def price_series(context: SignalContext, field: str = "close", *,
                 adjusted: bool = True) -> dict[str, list[float]]:
    """Per-stock price history for factors.

    ``adjusted`` (default) uses the price-only reference-price adjusted close
    when the panel supplies one (``Bar.adj_close``), falling back to raw close for
    synthetic panels without adjustment.  Execution never reads this series.
    """
    output = {}
    for stock_id, bars in _bars_by_stock(context).items():
        if field == "close" and adjusted:
            values = [_number(bar.adj_close if bar.adj_close is not None else bar.close)
                      for bar in bars]
        else:
            values = [_number(getattr(bar, field, None)) for bar in bars]
        output[stock_id] = [value for value in values if value is not None and value > 0]
    return output


def momentum(context: SignalContext, lookback: int = 252,
             skip_recent: int = 21, *, adjusted: bool = True) -> dict[str, float | None]:
    if lookback <= skip_recent or skip_recent < 0:
        raise ValueError("momentum lookback must exceed non-negative skip_recent")
    output = {}
    for stock_id, closes in price_series(context, adjusted=adjusted).items():
        if len(closes) <= lookback:
            output[stock_id] = None
            continue
        start, end = closes[-lookback - 1], closes[-skip_recent - 1]
        output[stock_id] = end / start - 1 if start > 0 else None
    return output


def moving_average_distance(context: SignalContext, lookback: int = 200, *,
                            adjusted: bool = True) -> dict[str, float | None]:
    if lookback < 2:
        raise ValueError("moving-average lookback must be at least 2")
    output = {}
    for stock_id, closes in price_series(context, adjusted=adjusted).items():
        if len(closes) < lookback:
            output[stock_id] = None
            continue
        average = statistics.fmean(closes[-lookback:])
        output[stock_id] = closes[-1] / average - 1 if average > 0 else None
    return output


def annualized_volatility(context: SignalContext, lookback: int = 126, *,
                          adjusted: bool = True) -> dict[str, float | None]:
    if lookback < 2:
        raise ValueError("volatility lookback must be at least 2")
    output = {}
    for stock_id, closes in price_series(context, adjusted=adjusted).items():
        if len(closes) < lookback + 1:
            output[stock_id] = None
            continue
        sample = closes[-lookback - 1:]
        returns = [math.log(sample[index] / sample[index - 1])
                   for index in range(1, len(sample))]
        output[stock_id] = statistics.stdev(returns) * math.sqrt(252)
    return output


def liquidity(context: SignalContext, lookback: int = 120,
              min_observations: int = 120) -> dict[str, float | None]:
    """Average trading amount through signal date T; never uses future bars."""
    if lookback < 1 or min_observations < 1 or min_observations > lookback:
        raise ValueError("liquidity observations must be within lookback")
    output = {}
    for stock_id, bars in _bars_by_stock(context).items():
        amounts = [_number(bar.amount) for bar in bars[-lookback:]]
        valid = [value for value in amounts if value is not None and value >= 0]
        output[stock_id] = statistics.fmean(valid) if len(valid) >= min_observations else None
    return output


def investable_universe(context: SignalContext, *, lookback: int = 120,
                        min_observations: int = 120,
                        min_average_amount: float = 30_000_000,
                        security_types: Sequence[str] = ("stock",)) -> set[str]:
    """Apply the shared PIT liquidity/listing filter used by all templates."""
    liquid = liquidity(context, lookback, min_observations)
    output = set()
    for stock_id, amount in liquid.items():
        if amount is None or amount < min_average_amount or stock_id not in context.bars:
            continue
        security = context.securities.get(stock_id)
        if security:
            if security.security_type and security.security_type not in security_types:
                continue
            if security.listed_date and security.listed_date >= context.date:
                continue
            if security.delisted_date and security.delisted_date <= context.date:
                continue
        output.add(stock_id)
    return output


def _fundamental_rows(context: SignalContext, dataset: str, stock_id: str) -> list[Mapping]:
    allowed = {"actual", "observed_finmind"}
    if context.publication_mode == "assumed":
        allowed.add("assumed_deadline")
    rows = [row for row in context.fundamentals.get(dataset, {}).get(stock_id, ())
            if row.get("publish_source") in allowed and
            str(row.get("publish_date", "")) < context.date]
    return sorted(rows, key=lambda row: str(row.get("period", "")))


def revenue_yoy(context: SignalContext) -> dict[str, float | None]:
    """Latest visible monthly YoY; derive from same-month revenue when needed."""
    output = {}
    for stock_id in context.securities.keys() | context.fundamentals.get("month_revenue", {}).keys():
        rows = _fundamental_rows(context, "month_revenue", stock_id)
        if not rows:
            output[stock_id] = None
            continue
        latest = rows[-1]
        explicit = _number(latest.get("yoy"))
        if explicit is not None:
            output[stock_id] = explicit / 100
            continue
        revenue = _number(latest.get("revenue"))
        try:
            year, month = map(int, str(latest["period"]).split("-"))
            prior_period = f"{year - 1:04d}-{month:02d}"
        except (KeyError, TypeError, ValueError):
            output[stock_id] = None
            continue
        prior = next((row for row in rows if row.get("period") == prior_period), None)
        previous = _number(prior.get("revenue")) if prior else None
        output[stock_id] = revenue / previous - 1 if revenue is not None and previous and previous > 0 else None
    return output


def revenue_acceleration(context: SignalContext, window: int = 3) -> dict[str, float | None]:
    """Change in average visible monthly YoY: latest window minus prior window."""
    if window < 1:
        raise ValueError("revenue acceleration window must be positive")
    output = {}
    stocks = context.securities.keys() | context.fundamentals.get("month_revenue", {}).keys()
    for stock_id in stocks:
        rows = _fundamental_rows(context, "month_revenue", stock_id)
        revenue_by_period = {str(row.get("period")): _number(row.get("revenue")) for row in rows}
        growth = []
        for row in rows:
            explicit = _number(row.get("yoy"))
            if explicit is not None:
                growth.append(explicit / 100)
                continue
            try:
                year, month = map(int, str(row["period"]).split("-"))
            except (KeyError, TypeError, ValueError):
                continue
            current = _number(row.get("revenue"))
            previous = revenue_by_period.get(f"{year - 1:04d}-{month:02d}")
            if current is not None and previous and previous > 0:
                growth.append(current / previous - 1)
        if len(growth) < window * 2:
            output[stock_id] = None
        else:
            output[stock_id] = (statistics.fmean(growth[-window:]) -
                                statistics.fmean(growth[-window * 2:-window]))
    return output


def financial_quality(context: SignalContext) -> dict[str, dict[str, float | None]]:
    """ROE, cash conversion, gross margin, and leverage from visible reports."""
    result = {name: {} for name in ("roe", "cash_conversion", "gross_margin", "low_debt")}
    stocks = context.securities.keys() | context.fundamentals.get("financials", {}).keys()
    for stock_id in stocks:
        rows = _fundamental_rows(context, "financials", stock_id)
        latest = rows[-1] if rows else {}
        trailing = rows[-4:]
        net_income = [_number(row.get("net_income")) for row in trailing]
        op_cashflow = [_number(row.get("op_cashflow")) for row in trailing]
        ni = sum(net_income) if len(trailing) == 4 and all(v is not None for v in net_income) else None
        ocf = sum(op_cashflow) if len(trailing) == 4 and all(v is not None for v in op_cashflow) else None
        equity = _number(latest.get("equity"))
        assets = _number(latest.get("total_assets"))
        liabilities = _number(latest.get("total_liabilities"))
        revenue = _number(latest.get("revenue"))
        gross_profit = _number(latest.get("gross_profit"))
        result["roe"][stock_id] = ni / equity if ni is not None and equity and equity > 0 else None
        result["cash_conversion"][stock_id] = ocf / ni if ocf is not None and ni and ni > 0 else None
        result["gross_margin"][stock_id] = (gross_profit / revenue
                                               if gross_profit is not None and revenue and revenue > 0 else None)
        result["low_debt"][stock_id] = (1 - liabilities / assets
                                           if liabilities is not None and assets and assets > 0 else None)
    return result


def financial_growth(context: SignalContext) -> dict[str, float | None]:
    """Visible TTM financial-statement revenue growth over the prior TTM."""
    output = {}
    stocks = context.securities.keys() | context.fundamentals.get("financials", {}).keys()
    for stock_id in stocks:
        rows = _fundamental_rows(context, "financials", stock_id)
        values = [_number(row.get("revenue")) for row in rows[-8:]]
        if len(values) < 8 or any(value is None for value in values):
            output[stock_id] = None
        else:
            previous, current = sum(values[:4]), sum(values[4:])
            output[stock_id] = current / previous - 1 if previous > 0 else None
    return output


def valuation_factors(context: SignalContext, max_age_days: int = 10) -> dict[str, dict[str, float | None]]:
    """Earnings/book yields from the latest dated valuation known by T."""
    result = {"earnings_yield": {}, "book_yield": {}, "dividend_yield": {}}
    import datetime as dt
    as_of = dt.date.fromisoformat(context.date)
    for stock_id in context.securities.keys() | context.valuations.keys():
        value = context.valuations.get(stock_id)
        age = (as_of - dt.date.fromisoformat(value.date)).days if value else None
        stale = value is None or age is None or age < 0 or age > max_age_days
        pe, pb = (_number(value.pe), _number(value.pb)) if value and not stale else (None, None)
        dy = _number(value.dividend_yield) if value and not stale else None
        result["earnings_yield"][stock_id] = 1 / pe if pe is not None and pe > 0 else None
        result["book_yield"][stock_id] = 1 / pb if pb is not None and pb > 0 else None
        result["dividend_yield"][stock_id] = dy / 100 if dy is not None else None
    return result


def sector_neutral_rank(values: Mapping[str, float | None], context: SignalContext, *,
                        minimum_group_size: int = 2,
                        missing: MissingPolicy = "exclude",
                        winsor_limits: tuple[float, float] = (.05, .95)) -> tuple[dict[str, float | None], tuple[str, ...]]:
    """Rank inside reliable PIT sectors; fall back globally with a warning."""
    reliable_sources = {"actual", "historical", "point_in_time"}
    groups = defaultdict(dict)
    fallback = {}
    for stock_id, value in values.items():
        security = context.securities.get(stock_id)
        if (security and security.industry and
                security.industry_source in reliable_sources):
            groups[security.industry][stock_id] = value
        else:
            fallback[stock_id] = value
    global_ranks = ranked_factor(values, winsor_limits=winsor_limits, missing=missing)
    output = {}
    small = []
    for sector, members in groups.items():
        valid_count = sum(_number(value) is not None for value in members.values())
        if valid_count < minimum_group_size:
            small.append(sector)
            output.update({stock_id: global_ranks[stock_id] for stock_id in members})
        else:
            output.update(ranked_factor(members, winsor_limits=winsor_limits, missing=missing))
    output.update({stock_id: global_ranks[stock_id] for stock_id in fallback})
    warnings = []
    if fallback:
        warnings.append(
            "sector-neutral fallback: reliable point-in-time sector metadata unavailable for "
            f"{len(fallback)} securities; global ranking used")
    if small:
        warnings.append(
            "sector-neutral fallback: groups below minimum size used global ranking: " +
            ",".join(sorted(small)))
    return output, tuple(warnings)
