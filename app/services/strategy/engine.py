"""Pure event-driven backtest engine.

Execution model: observe the point-in-time panel on signal date T, size the
target portfolio after that observation, and execute no earlier than trading
session T+1.  The engine has no database imports and cannot retrieve future
data.  Net and frictionless gross ledgers are simulated side by side.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import costs, metrics, rules
from .types import (BacktestConfig, Bar, CorporateAction, Panel, SignalContext,
                    SignalRule)


@dataclass
class _Lot:
    shares: float
    entry_date: str
    raw_cost: float
    net_cost: float


@dataclass
class _Ledger:
    cash: float
    lots: dict[str, list[_Lot]] = field(default_factory=dict)

    def shares(self, stock_id: str) -> float:
        return sum(x.shares for x in self.lots.get(stock_id, ()))


@dataclass
class _Order:
    signal_date: str
    due_session: int
    targets: dict[str, float]


def _price(bar: Bar | None, field_name: str) -> float | None:
    value = getattr(bar, field_name, None) if bar else None
    return float(value) if value is not None and math.isfinite(float(value)) and value > 0 else None


def _round_shares(value: float, lot_size: float) -> float:
    return max(0.0, math.floor((value + 1e-12) / lot_size) * lot_size)


def _equity(ledger: _Ledger, prices: Mapping[str, float]) -> float:
    return ledger.cash + sum(ledger.shares(sid) * prices.get(sid, 0.0)
                             for sid in ledger.lots)


def _consume_lots(ledger: _Ledger, stock_id: str, shares: float, exit_date: str,
                  raw_proceeds: float, net_proceeds: float) -> dict | None:
    remaining, raw_cost, net_cost, weighted_days = shares, 0.0, 0.0, 0.0
    lots = ledger.lots.get(stock_id, [])
    entry_dates = []
    while remaining > 1e-9 and lots:
        lot = lots[0]
        taken = min(remaining, lot.shares)
        fraction = taken / lot.shares
        raw_cost += lot.raw_cost * fraction
        net_cost += lot.net_cost * fraction
        days = (dt.date.fromisoformat(exit_date) - dt.date.fromisoformat(lot.entry_date)).days
        weighted_days += days * taken
        entry_dates.append(lot.entry_date)
        lot.raw_cost *= 1 - fraction
        lot.net_cost *= 1 - fraction
        lot.shares -= taken
        remaining -= taken
        if lot.shares <= 1e-9:
            lots.pop(0)
    if not lots:
        ledger.lots.pop(stock_id, None)
    sold = shares - remaining
    if sold <= 0:
        return None
    return {
        "stock_id": stock_id, "entry_date": min(entry_dates), "exit_date": exit_date,
        "shares": sold, "gross_pnl": raw_proceeds * sold / shares - raw_cost,
        "net_pnl": net_proceeds * sold / shares - net_cost,
        "holding_days": weighted_days / sold,
    }


def _sell(ledger: _Ledger, stock_id: str, shares: float, date: str,
          reference_price: float, config: BacktestConfig, *, net: bool,
          reason: str, fills: list[dict], closed: list[dict]) -> None:
    shares = min(shares, ledger.shares(stock_id))
    if shares <= 1e-9:
        return
    if net:
        fee = costs.calculate("sell", shares, reference_price, config.costs)
        proceeds = fee.notional - fee.commission - fee.tax
        ledger.cash += proceeds
        trade = _consume_lots(ledger, stock_id, shares, date,
                              shares * reference_price, proceeds)
        if trade:
            trade["exit_reason"] = reason
            closed.append(trade)
        fills.append({
            "date": date, "stock_id": stock_id, "side": "sell", "shares": shares,
            "reference_price": reference_price, "fill_price": fee.fill_price,
            "reference_notional": shares * reference_price,
            "commission": fee.commission, "tax": fee.tax,
            "slippage": fee.slippage, "reason": reason,
        })
    else:
        proceeds = shares * reference_price
        ledger.cash += proceeds
        _consume_lots(ledger, stock_id, shares, date, proceeds, proceeds)


def _buy(ledger: _Ledger, stock_id: str, shares: float, date: str,
         reference_price: float, config: BacktestConfig, *, net: bool,
         fills: list[dict]) -> None:
    if shares <= 1e-9:
        return
    if net:
        fee = costs.calculate("buy", shares, reference_price, config.costs)
        required = fee.notional + fee.commission
        lot = config.sizing.lot_size
        while shares > 1e-9 and required > ledger.cash + 1e-9:
            shares = _round_shares(shares - lot, lot)
            fee = costs.calculate("buy", shares, reference_price, config.costs)
            required = fee.notional + fee.commission
        if shares <= 1e-9:
            return
        ledger.cash -= required
        ledger.lots.setdefault(stock_id, []).append(
            _Lot(shares, date, shares * reference_price, required))
        fills.append({
            "date": date, "stock_id": stock_id, "side": "buy", "shares": shares,
            "reference_price": reference_price, "fill_price": fee.fill_price,
            "reference_notional": shares * reference_price,
            "commission": fee.commission, "tax": 0.0,
            "slippage": fee.slippage, "reason": "rebalance",
        })
    else:
        required = shares * reference_price
        if required > ledger.cash:
            shares = _round_shares(ledger.cash / reference_price, config.sizing.lot_size)
            required = shares * reference_price
        if shares <= 1e-9:
            return
        ledger.cash -= required
        ledger.lots.setdefault(stock_id, []).append(_Lot(shares, date, required, required))


def _rebalance(ledger: _Ledger, targets: Mapping[str, float], date: str,
               prices: Mapping[str, float], config: BacktestConfig, *, net: bool,
               fills: list[dict], closed: list[dict],
               marks: Mapping[str, float] | None = None) -> None:
    """``marks`` (partial mode only) values frozen holdings that have no execution
    price; they are neither bought nor sold, so their shares never change."""
    equity = _equity(ledger, {**marks, **prices} if marks else prices)
    all_ids = set(ledger.lots) | set(targets)
    desired = {sid: _round_shares(equity * targets.get(sid, 0.0) / prices[sid],
                                  config.sizing.lot_size)
               for sid in all_ids if sid in prices}
    for sid in sorted(all_ids):
        current = ledger.shares(sid)
        wanted = desired.get(sid, current)
        if wanted + 1e-9 < current and sid in prices:
            _sell(ledger, sid, current - wanted, date, prices[sid], config,
                  net=net, reason="rebalance", fills=fills, closed=closed)
    for sid in sorted(targets, key=lambda x: targets[x], reverse=True):
        current = ledger.shares(sid)
        wanted = desired.get(sid, current)
        if wanted > current + 1e-9 and sid in prices:
            _buy(ledger, sid, wanted - current, date, prices[sid], config,
                 net=net, fills=fills)


_CASH_KINDS = ("dividend_cash", "cash_dividend")


def _apply_action(ledger: _Ledger, action: CorporateAction, warnings: list[str],
                  unresolved: list[dict] | None = None) -> None:
    """Portfolio accounting only (cash and share counts); factor prices are separate.

    Cash is credited only from an explicit ``cash_dividend``; shares change only
    from an explicit ``share_multiplier``.  ``ratio`` is never interpreted here.
    An event whose cash/share effect stays unknown while shares are held is
    recorded in ``unresolved`` (contract: ``UNRESOLVED_CORPORATE_ACTION``) so
    callers can exclude the holding period instead of trusting its P&L.
    """
    shares = ledger.shares(action.stock_id)
    if shares <= 0:
        return
    if action.cash_dividend is not None:
        ledger.cash += shares * float(action.cash_dividend)
    known = action.cash_dividend is not None and action.kind in _CASH_KINDS
    if action.share_multiplier is not None:
        multiplier = float(action.share_multiplier)
        if multiplier <= 0:
            warnings.append(f"{action.date} {action.stock_id}: invalid share_multiplier ignored")
        else:
            for lot in ledger.lots.get(action.stock_id, ()):
                lot.shares *= multiplier
            known = True
    if known:
        return
    warnings.append(
        f"UNRESOLVED_CORPORATE_ACTION {action.date} {action.stock_id}: {action.kind}"
        f" ({action.resolution or 'no resolution'}) has no verified cash/share effect; "
        f"ratio was not interpreted; P&L across this event is unreliable")
    if unresolved is not None:
        unresolved.append({"date": action.date, "stock_id": action.stock_id,
                           "kind": action.kind, "resolution": action.resolution,
                           "shares_held": shares})


def run_backtest(panel: Panel, signal_rule: SignalRule, config: BacktestConfig | None = None) -> dict:
    config = config or BacktestConfig()
    trading_dates = [d.date for d in panel.days if d.is_trading_session]
    net = _Ledger(config.initial_cash)
    gross = _Ledger(config.initial_cash)
    pending: list[_Order] = []
    last_prices: dict[str, float] = {}
    last_seen: dict[str, int] = {}
    fundamentals: dict[str, dict[str, list[dict[str, Any]]]] = {
        "month_revenue": {}, "financials": {}}
    fills: list[dict] = []
    closed: list[dict] = []
    warnings = list(panel.warnings)
    unresolved: list[dict] = []
    partial_events: list[dict] = []
    valuations = {}
    curve: list[dict] = []
    benchmark_base = next((d.benchmark for d in panel.days if d.benchmark and d.benchmark > 0), None)
    delisted: set[str] = set()
    session_index = -1

    for index, day in enumerate(panel.days):
        if day.is_trading_session:
            session_index += 1
        for sid, bar in day.bars.items():
            close = _price(bar, "close")
            if close is not None:
                last_prices[sid], last_seen[sid] = close, session_index
        for publication in day.publications:
            row = {"period": publication.period, "publish_date": publication.publish_date,
                   "publish_source": publication.publish_source, **publication.values}
            fundamentals[publication.dataset].setdefault(publication.stock_id, []).append(row)
        valuations.update(day.valuations)
        for action in day.actions:
            _apply_action(net, action, warnings, unresolved)
            _apply_action(gross, action, warnings)

        # Delisting is an explicit forced exit at the latest observed close.  It
        # never invents a liquidation value after the final quote.
        for sid, security in panel.securities.items():
            if (security.delisted_date and security.delisted_date <= day.date
                    and sid not in delisted):
                if net.shares(sid) > 0 or gross.shares(sid) > 0:
                    px = _price(day.bars.get(sid), "close") or last_prices.get(sid)
                    if px is None:
                        warnings.append(f"{day.date} {sid}: delisting exit unavailable; no valid price")
                    else:
                        _sell(net, sid, net.shares(sid), day.date, px, config, net=True,
                              reason="delisted", fills=fills, closed=closed)
                        _sell(gross, sid, gross.shares(sid), day.date, px, config, net=False,
                              reason="delisted", fills=[], closed=[])
                delisted.add(sid)

        still_pending = []
        for order in pending:
            if not day.is_trading_session or order.due_session > session_index:
                still_pending.append(order)
                continue
            needed = set(order.targets) | set(net.lots) | set(gross.lots)
            exec_prices = {sid: _price(day.bars.get(sid), config.execution.price) for sid in needed}
            missing = [sid for sid, value in exec_prices.items() if value is None and sid not in delisted]
            frozen: dict[str, float] = {}
            if missing and config.missing.execution == "partial":
                tradable = [sid for sid, value in exec_prices.items() if value is not None]
                if tradable:
                    # Frozen names keep their shares and are valued at the last close; a frozen
                    # target that is not held simply stays unbought (its weight stays cash).
                    frozen = {sid: last_prices[sid] for sid in missing if sid in last_prices}
                    partial_events.append({"date": day.date, "signal_date": order.signal_date,
                                           "frozen": sorted(missing), "executed": len(tradable)})
                    warnings.append(f"{day.date}: PARTIAL_REBALANCE execution price missing for "
                                    f"{','.join(sorted(missing))}; frozen, rest executed")
                    missing = []
            if missing:
                message = f"{day.date}: execution price missing for {','.join(sorted(missing))}"
                if config.missing.execution in ("next_available", "partial"):
                    still_pending.append(order)
                    warnings.append(message + "; deferred")
                else:
                    warnings.append(message + "; cancelled")
                continue
            prices = {sid: value for sid, value in exec_prices.items() if value is not None}
            _rebalance(net, order.targets, day.date, prices, config, net=True,
                       fills=fills, closed=closed, marks=frozen)
            _rebalance(gross, order.targets, day.date, prices, config, net=False,
                       fills=[], closed=[], marks=frozen)
            for sid, value in prices.items():
                if ((net.shares(sid) > 0 or gross.shares(sid) > 0)
                        and sid not in last_prices):
                    if config.missing.valuation == "error":
                        raise ValueError(f"{day.date}: no closing valuation for new position {sid}")
                    last_prices[sid], last_seen[sid] = value, session_index
                    warnings.append(
                        f"{day.date} {sid}: closing price missing; execution price used for valuation")
        pending = still_pending

        if (day.is_trading_session and
                rules.is_rebalance_day(trading_dates, session_index, config.rebalance)):
            context = SignalContext(day.date, index, day.bars,
                                    tuple(panel.days[:index + 1]),
                                    {dataset: {sid: tuple(rows) for sid, rows in stocks.items()}
                                     for dataset, stocks in fundamentals.items()},
                                    dict(valuations), panel.securities, panel.publication_mode)
            signal = signal_rule(context)
            if signal is not None:
                if hasattr(signal, "warnings"):
                    warnings.extend(str(value) for value in signal.warnings)
                targets = rules.target_weights(signal, config.sizing)
                due = session_index + config.execution.delay_sessions
                if due < len(trading_dates):
                    if pending:
                        warnings.append(f"{day.date}: newer signal replaced unfilled target order")
                        pending.clear()
                    pending.append(_Order(day.date, due, targets))
                else:
                    warnings.append(f"{day.date}: signal falls beyond backtest execution window")

        mark_prices = dict(last_prices)
        for sid in set(net.lots) | set(gross.lots):
            stale = session_index - last_seen.get(sid, session_index)
            if sid not in mark_prices:
                if config.missing.valuation == "error":
                    raise ValueError(f"{day.date}: no valuation price for {sid}")
                warnings.append(f"{day.date} {sid}: no valuation price")
            elif config.missing.max_stale_sessions is not None and stale > config.missing.max_stale_sessions:
                message = f"{day.date} {sid}: valuation is {stale} sessions stale"
                if config.missing.valuation == "error":
                    raise ValueError(message)
                warnings.append(message)
        net_equity = _equity(net, mark_prices)
        gross_equity = _equity(gross, mark_prices)
        curve.append({
            "date": day.date, "net_equity": net_equity, "gross_equity": gross_equity,
            "benchmark": (config.initial_cash * day.benchmark / benchmark_base
                          if benchmark_base and day.benchmark else None),
        })

    result_metrics = metrics.compute(curve, closed, fills, config.risk_free_rate)
    return {
        "metadata": {
            "publication_mode": panel.publication_mode,
            "benchmark_id": panel.benchmark_id,
            "execution_model": f"signal T -> earliest T+{config.execution.delay_sessions} {config.execution.price}",
            "config": config.to_dict(),
            # Held positions that crossed a corporate action with unknown cash/share
            # effect; Stage E should exclude or flag these holding periods.
            "unresolved_actions": unresolved,
            "unresolved_action_count": len(unresolved),
            # Stage F0: rebalances executed with some names frozen (missing.execution="partial").
            "partial_rebalances": partial_events,
            "partial_rebalance_count": len(partial_events),
        },
        "metrics": result_metrics,
        "equity_curve": curve,
        "fills": fills,
        "closed_trades": closed,
        "warnings": list(dict.fromkeys(warnings)),
        "open_positions": {sid: net.shares(sid) for sid in net.lots if net.shares(sid) > 0},
        "ending_cash": net.cash,
    }
