"""Pure backtest metrics using the project's established return conventions.

CAGR uses actual calendar days / 365.25.  Annualized volatility uses sample
standard deviation of daily log returns times sqrt(252), matching
``finance.etf_stats.compute``.  Percentage-valued outputs are in percent.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import mean
from typing import Mapping, Sequence

TRADING_DAYS = 252


def _finite(values):
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def equity_statistics(curve: Sequence[Mapping], value_key: str = "equity") -> dict:
    rows = [r for r in curve if r.get(value_key) is not None
            and math.isfinite(float(r[value_key])) and float(r[value_key]) >= 0]
    if not rows:
        return {"total_return": None, "cagr": None,
                "annualized_volatility": None, "max_drawdown": None,
                "recovery_time": None, "daily_returns": [], "drawdowns": []}
    values = [float(r[value_key]) for r in rows]
    dates = [dt.date.fromisoformat(str(r["date"])) for r in rows]
    if values[0] <= 0:
        return {"total_return": None, "cagr": None,
                "annualized_volatility": None, "max_drawdown": None,
                "recovery_time": None, "daily_returns": [], "drawdowns": []}
    total = values[-1] / values[0] - 1
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = ((values[-1] / values[0]) ** (1 / years) - 1
            if years > 0 and values[-1] > 0 else None)
    log_returns = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))
                   if values[i] > 0 and values[i - 1] > 0]
    vol = None
    if len(log_returns) > 1:
        avg = mean(log_returns)
        variance = sum((r - avg) ** 2 for r in log_returns) / (len(log_returns) - 1)
        vol = math.sqrt(variance * TRADING_DAYS)
    daily = [values[i] / values[i - 1] - 1 for i in range(1, len(values))
             if values[i - 1] > 0]
    peak = values[0]
    peak_date = dates[0]
    drawdowns, mdd, recoveries = [], 0.0, []
    underwater_since = None
    for date, value in zip(dates, values):
        if value >= peak:
            if underwater_since is not None:
                recoveries.append((date - underwater_since).days)
                underwater_since = None
            peak, peak_date = value, date
        else:
            if underwater_since is None:
                underwater_since = peak_date
        dd = value / peak - 1
        drawdowns.append(dd)
        mdd = min(mdd, dd)
    return {
        "total_return": total * 100,
        "cagr": cagr * 100 if cagr is not None else None,
        "annualized_volatility": vol * 100 if vol is not None else None,
        "max_drawdown": mdd * 100,
        "recovery_time": max(recoveries) if recoveries else None,
        "daily_returns": daily,
        "drawdowns": drawdowns,
    }


def _risk_metrics(returns: Sequence[float], risk_free_rate: float) -> tuple[float | None, float | None]:
    if len(returns) < 2:
        return None, None
    daily_rf = (1 + risk_free_rate) ** (1 / TRADING_DAYS) - 1
    excess = [r - daily_rf for r in returns]
    avg = mean(excess)
    variance = sum((r - avg) ** 2 for r in excess) / (len(excess) - 1)
    sharpe = avg / math.sqrt(variance) * math.sqrt(TRADING_DAYS) if variance > 0 else None
    downside = [min(r, 0.0) for r in excess]
    downside_dev = math.sqrt(sum(r * r for r in downside) / len(downside))
    sortino = avg / downside_dev * math.sqrt(TRADING_DAYS) if downside_dev > 0 else None
    return sharpe, sortino


def _benchmark_metrics(returns: Sequence[float], benchmark: Sequence[float],
                       risk_free_rate: float) -> tuple[float | None, float | None]:
    n = min(len(returns), len(benchmark))
    if n < 2:
        return None, None
    rs, rb = list(returns)[-n:], list(benchmark)[-n:]
    mb = mean(rb)
    variance = sum((x - mb) ** 2 for x in rb) / (n - 1)
    if variance <= 0:
        return None, None
    ms = mean(rs)
    covariance = sum((a - ms) * (b - mb) for a, b in zip(rs, rb)) / (n - 1)
    beta = covariance / variance
    daily_rf = (1 + risk_free_rate) ** (1 / TRADING_DAYS) - 1
    alpha = ((ms - daily_rf) - beta * (mb - daily_rf)) * TRADING_DAYS
    return beta, alpha * 100


def trade_statistics(closed_trades: Sequence[Mapping]) -> dict:
    """Trade-level metrics with explicit journal-compatibility semantics.

    ``payoff_ratio`` preserves the formula historically exposed by
    ``journal.performance()`` as ``profit_factor``.  Strategy backtests use the
    standard ``profit_factor`` definition: gross profit / absolute gross loss.
    Both ratios are undefined (``None``) when their denominator is zero so the
    result stays JSON-safe instead of emitting infinity.
    """
    pnls = [float(t.get("net_pnl", t.get("pnl", 0))) for t in closed_trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    avg_win = mean(wins) if wins else 0.0
    avg_loss = abs(mean(losses)) if losses else 0.0
    win_rate = len(wins) / len(pnls) if pnls else None
    # Compatibility metric: journal.performance() historically calls this
    # profit_factor.  Keep journal unchanged, but never expose that name here.
    payoff_ratio = avg_win / avg_loss if avg_loss else None
    gross_profit = sum(wins)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    expectancy = (win_rate * avg_win - (1 - win_rate) * avg_loss
                  if win_rate is not None else None)
    holding = _finite(t.get("holding_days") for t in closed_trades)
    return {
        "win_rate": win_rate * 100 if win_rate is not None else None,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "expectancy": expectancy,
        "average_holding_period": mean(holding) if holding else None,
        "number_of_trades": len(pnls),
    }


def compute(curve: Sequence[Mapping], closed_trades: Sequence[Mapping] = (),
            fills: Sequence[Mapping] = (), risk_free_rate: float = 0.0) -> dict:
    """Compute the Stage B metric set from engine output."""
    net = equity_statistics(curve, "net_equity")
    gross = equity_statistics(curve, "gross_equity")
    sharpe, sortino = _risk_metrics(net["daily_returns"], risk_free_rate)
    paired_strategy, benchmark_returns = [], []
    for previous, current in zip(curve, curve[1:]):
        if (previous.get("benchmark") is not None and current.get("benchmark") is not None
                and previous["benchmark"] > 0 and previous["net_equity"] > 0):
            paired_strategy.append(current["net_equity"] / previous["net_equity"] - 1)
            benchmark_returns.append(current["benchmark"] / previous["benchmark"] - 1)
    beta, alpha = _benchmark_metrics(paired_strategy, benchmark_returns, risk_free_rate)
    trades = trade_statistics(closed_trades)
    average_equity = mean([float(r["net_equity"]) for r in curve]) if curve else None
    traded = sum(abs(float(f.get("reference_notional", 0))) for f in fills)
    turnover = traded / average_equity * 100 if average_equity else None
    mdd = abs(net["max_drawdown"] or 0)
    calmar = net["cagr"] / mdd if mdd and net["cagr"] is not None else None
    return {
        "total_return": net["total_return"], "cagr": net["cagr"],
        "annualized_volatility": net["annualized_volatility"],
        "sharpe": sharpe, "sortino": sortino,
        "max_drawdown": net["max_drawdown"], "calmar": calmar,
        "beta": beta, "alpha": alpha,
        **trades,
        "turnover": turnover, "recovery_time": net["recovery_time"],
        "gross_return": gross["total_return"], "net_return": net["total_return"],
    }
