"""Stage H1 Tasks 5-7: the portfolio research-run engine.

Pure function (no DB access — the caller, ``app.services.portfolio_lab``,
assembles every input from the DB first). One call replays an ENTIRE portfolio
research window day by day:

    for each date:  apply corporate actions on currently-held shares (same
    semantics as ``portfolio.ledger``, cross-checked against the Stage B engine
    in Stage H0) -> IF this is a rebalance date, resolve the aggregate target
    (``portfolio.target.resolve_rebalance_target``) and trade toward it (sells
    first to raise cash, then buys, scaled down deterministically if cash is
    short) -> mark every held position to market -> record the day.

Only ONE order per stock is ever computed and costed per rebalance date — the
aggregate target weight already nets every component's desired exposure to that
stock (``netting.aggregate_stock_exposure``) before any share count or cost is
derived, so five components wanting the same stock never pay five separate
costs (Task 6's "no naive-summed cost" requirement is structural here, not a
separate reconciliation step).
"""
from __future__ import annotations

from typing import Callable, Mapping, Sequence

from app.services.strategy import costs as costs_mod
from app.services.strategy.types import CostConfig

from . import series as series_mod
from . import target as target_mod

RESEARCH_ENGINE_VERSION = "portfolio-research-engine/h1/1"
IMPLEMENTED_NO_CANDIDATE_POLICIES = ("cash_hold",)
ZERO_SHARE_EPS = 1e-9
ZERO_DELTA_EPS = 1e-6


def _mark(shares: Mapping[str, float], closes: Mapping[str, Mapping[str, float]], date: str,
         last_close: dict[str, float]) -> dict[str, float | None]:
    """Forward-filled mark-to-market for currently held shares — identical
    semantics to ``portfolio.ledger.replay_sleeve``'s own marking step: a
    position whose price was never observed contributes ``None`` (0 to the
    total), never a fabricated non-zero mark."""
    for sid in shares:
        px = closes.get(sid, {}).get(date)
        if px is not None:
            last_close[sid] = px
    return {sid: (shares[sid] * last_close[sid] if sid in last_close else None) for sid in shares}


def _execute_orders(orders: Mapping[str, float], shares: dict[str, float], cash: float,
                    closes: Mapping[str, Mapping[str, float]], date: str,
                    cost_config: CostConfig) -> tuple[float, float, dict, list[dict]]:
    """Sells execute first (they raise cash), then buys in deterministic
    (sorted stock_id) order, scaled down — never skipped outright while any
    cash remains — if the full amount would overdraw cash. Returns
    ``(new_cash, cost_total, trade_rows, diagnostics)``."""
    cost_total = 0.0
    trade_rows: dict[str, dict] = {}
    diagnostics: list[dict] = []
    sells = {sid: d for sid, d in orders.items() if d < 0}
    buys = {sid: d for sid, d in orders.items() if d > 0}
    for sid in sorted(sells):
        delta = sells[sid]
        price = closes[sid][date]
        bd = costs_mod.calculate("sell", abs(delta), price, cost_config)
        cash += bd.notional - bd.commission - bd.tax
        shares[sid] = shares.get(sid, 0.0) + delta
        if abs(shares[sid]) < ZERO_SHARE_EPS:
            shares.pop(sid, None)
        cost_total += bd.total
        trade_rows[sid] = {"delta_shares": delta, "side": "sell", "cost": bd.total,
                           "reference_notional": abs(delta) * price}
    for sid in sorted(buys):
        delta = buys[sid]
        price = closes[sid][date]
        bd = costs_mod.calculate("buy", delta, price, cost_config)
        needed = bd.notional + bd.commission
        if needed > cash + 1e-6:
            if cash <= 1e-6:
                diagnostics.append({"stock_id": sid, "reason": "insufficient_cash",
                                    "requested_delta": delta, "executed_delta": 0.0})
                continue
            # Re-scale up to twice: commission is rounded to the nearest whole
            # currency unit, so a single rescale can still overshoot ``cash`` by
            # a sub-unit rounding step; a second pass removes that residual.
            for _ in range(2):
                scale = cash / needed
                delta = delta * scale
                bd = costs_mod.calculate("buy", delta, price, cost_config)
                needed = bd.notional + bd.commission
                if needed <= cash:
                    break
            diagnostics.append({"stock_id": sid, "reason": "insufficient_cash",
                                "requested_delta": buys[sid], "executed_delta": delta})
        cash -= needed
        shares[sid] = shares.get(sid, 0.0) + delta
        if abs(shares[sid]) < ZERO_SHARE_EPS:
            shares.pop(sid, None)
        cost_total += bd.total
        trade_rows[sid] = {"delta_shares": delta, "side": "buy", "cost": bd.total,
                           "reference_notional": abs(delta) * price}
    return cash, cost_total, trade_rows, diagnostics


def _rebalance_orders(target_weight: Mapping[str, float], shares: Mapping[str, float], equity: float,
                      closes: Mapping[str, Mapping[str, float]], date: str) -> tuple[dict[str, float], list[dict]]:
    """Desired share deltas for every target stock (price available) plus a full
    liquidation delta for any currently-held stock that has fallen out of the
    target. A target stock, or a stock needing liquidation, with no price
    observed today is DEFERRED (kept at its current share count, reported as a
    diagnostic) rather than traded on a fabricated price."""
    orders: dict[str, float] = {}
    deferred: list[dict] = []
    for sid, w in target_weight.items():
        price = closes.get(sid, {}).get(date)
        if price is None:
            deferred.append({"stock_id": sid, "reason": "missing_price", "target_weight": w})
            continue
        desired_shares = w * equity / price
        delta = desired_shares - shares.get(sid, 0.0)
        if abs(delta) > ZERO_DELTA_EPS:
            orders[sid] = delta
    for sid, held in shares.items():
        if sid in target_weight or held == 0:
            continue
        price = closes.get(sid, {}).get(date)
        if price is None:
            deferred.append({"stock_id": sid, "reason": "missing_price_suspended_liquidation",
                             "held_shares": held})
            continue
        orders[sid] = orders.get(sid, 0.0) - held
    return {sid: d for sid, d in orders.items() if abs(d) > ZERO_DELTA_EPS}, deferred


def run_portfolio_backtest(*, dates: Sequence[str], rebalance_dates: Sequence[str],
                           component_keys: Sequence[str], component_series: Mapping[str, dict],
                           method: str, allocation_target_weight: Mapping[str, float] | None,
                           portfolio_constraints: Mapping, no_candidate_policy: str,
                           closes: Mapping[str, Mapping[str, float]],
                           actions_by_date: Mapping[str, Sequence[Mapping]],
                           cost_config: CostConfig, initial_cash: float,
                           cancel_check: Callable[[], bool] | None = None) -> dict:
    """``cancel_check`` (optional): polled once per date, BEFORE that date's work.
    A run whose cancel_check ever returns True stops immediately and reports
    ``cancelled: True`` with whatever ``daily``/``rebalances`` rows were built so
    far — the caller (``portfolio_lab.execute_research_run``) never persists a
    cancelled run's rows (it only writes results atomically for a fully
    completed pass), so a mid-run cancellation never leaves a partial,
    misleading result on the DB. Cooperative, not preemptive: the day already in
    progress when cancellation is requested always finishes running."""
    if no_candidate_policy not in IMPLEMENTED_NO_CANDIDATE_POLICIES:
        raise NotImplementedError(
            f"no_candidate_policy={no_candidate_policy!r} is not implemented in Stage H1; "
            f"only {IMPLEMENTED_NO_CANDIDATE_POLICIES} is (cash, never auto-redistributed)")

    rebalance_set = set(rebalance_dates)
    shares: dict[str, float] = {}
    cash = float(initial_cash)
    last_close: dict[str, float] = {}
    prev_equity = float(initial_cash)
    current_target_weight: dict[str, float] = {}
    daily_rows: list[dict] = []
    rebalance_rows: list[dict] = []
    warnings: list[dict] = []
    cancelled = False

    for d in dates:
        if cancel_check is not None and cancel_check():
            cancelled = True
            break
        for a in sorted(actions_by_date.get(d, ()), key=lambda x: x["stock_id"]):
            sid = a["stock_id"]
            held = shares.get(sid, 0.0)
            if held <= 0:
                continue
            cash_dividend = a.get("cash_dividend")
            if cash_dividend is not None:
                cash += held * float(cash_dividend)
            multiplier = a.get("share_multiplier")
            if multiplier is not None and float(multiplier) > 0:
                shares[sid] = held * float(multiplier)

        rebalance_row = None
        if d in rebalance_set:
            candidate_status = {k: component_series[k]["by_date"].get(d, {}).get("candidate_status", "no_candidate")
                                for k in component_keys}
            sleeve_stock_weight = {k: component_series[k]["by_date"].get(d, {}).get("stock_weight_by_stock", {})
                                   for k in component_keys}
            prior = (series_mod.prior_returns(component_series, as_of=d) if method == "inverse_volatility" else None)
            target = target_mod.resolve_rebalance_target(
                as_of=d, component_keys=component_keys, method=method, candidate_status=candidate_status,
                sleeve_stock_weight=sleeve_stock_weight, portfolio_constraints=portfolio_constraints,
                target_weight=allocation_target_weight, prior_returns_by_component=prior)
            if target["status"] != "ready":
                warnings.append({"date": d, "code": "ALLOCATION_NOT_READY", "status": target["status"],
                                 "reason": target.get("reason")})
            else:
                pre_mtm = _mark(shares, closes, d, dict(last_close))
                pre_equity = cash + sum(v for v in pre_mtm.values() if v is not None)
                orders, deferred = _rebalance_orders(target["stock_weight"], shares, pre_equity, closes, d)
                cash, cost_total, trade_rows, cash_diag = _execute_orders(orders, shares, cash, closes, d, cost_config)
                current_target_weight = target["stock_weight"]
                if deferred or cash_diag:
                    warnings.append({"date": d, "code": "PARTIAL_EXECUTION",
                                     "deferred": deferred, "cash_shortfalls": cash_diag})
                rebalance_row = {
                    "date": d, "target": target, "orders": trade_rows, "cost_total": cost_total,
                    "deferred": deferred, "cash_shortfalls": cash_diag, "pre_trade_equity": pre_equity,
                }

        mtm = _mark(shares, closes, d, last_close)
        total_stock_value = sum(v for v in mtm.values() if v is not None)
        total_equity = cash + total_stock_value
        missing_marks = sorted(sid for sid, v in mtm.items() if v is None)
        stock_weight_today = {
            sid: (v / total_equity if v is not None and total_equity else (None if v is None else 0.0))
            for sid, v in mtm.items()
        }
        cash_weight = cash / total_equity if total_equity else None
        daily_return = (total_equity / prev_equity - 1.0) if prev_equity else None

        daily_rows.append({
            "date": d, "cash": cash, "total_equity": total_equity, "daily_return": daily_return,
            "cash_weight": cash_weight, "missing_marks": missing_marks,
        })
        if rebalance_row is not None:
            rebalance_row["holdings"] = {
                sid: {"shares": shares.get(sid, 0.0), "mtm_value": mtm.get(sid), "weight": stock_weight_today.get(sid)}
                for sid in shares
            }
            rebalance_row["target_stock_weight"] = current_target_weight
            rebalance_row["contribution_by_stock"] = target["contribution_by_stock"]
            rebalance_row["constraint_diagnostics"] = target["constraint_diagnostics"]
            rebalance_row["cash_weight"] = cash_weight
            rebalance_row["total_equity"] = total_equity
            rebalance_rows.append(rebalance_row)
        prev_equity = total_equity

    return {
        "contract_version": RESEARCH_ENGINE_VERSION, "daily": daily_rows, "rebalances": rebalance_rows,
        "warnings": warnings, "final_shares": dict(shares), "final_cash": cash, "cancelled": cancelled,
    }
