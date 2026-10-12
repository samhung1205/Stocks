"""Stage H0 Task 1: exact daily sleeve ledger.

Reconstructs, for one evaluated Walk Forward fold, the SAME per-date state the
backtest engine (``app.services.strategy.engine``) held internally — shares held
per stock, cash, mark-to-market stock value, total equity — using only what is
already persisted: ``fill`` trades (``walkforward_trades``), corporate-action rows
(``corporate_actions``) and closing prices (``daily_quotes``). It never re-derives
a weight from entry notional (Stage G's ``cross_strategy/sleeve.py`` approximation);
every value here is a real daily mark-to-market.

Faithfulness to the engine (see ``engine.py``, cross-checked field by field):
- A fill's actual cash effect is fully determined by its own stored fields —
  ``shares * fill_price ± commission ± tax`` — never by re-deriving the cost model,
  so this module needs no ``CostConfig``. Buy: ``cash -= shares*fill_price + commission``.
  Sell: ``cash += shares*fill_price - commission - tax`` (mirrors ``_buy``/``_sell``).
- A corporate action changes the ledger only through its own ``cash_dividend`` /
  ``share_multiplier`` columns, exactly as ``_apply_action`` does, and only for a
  stock currently held (>0 shares) — an "unresolved" action (both fields NULL) is
  therefore a natural no-op here too, with no special-casing needed.
- End-of-day mark-to-market always uses the daily CLOSE, forward-filled from the
  most recent date a close was observed for that stock — this holds regardless of
  the template's own ``execution.price`` (open or close), because the engine's
  ``last_prices`` used for equity marking is refreshed from ``bar.close`` at the top
  of every day, independent of what price was used to fill an order that day.
- A held position with literally no close ever observed contributes 0 to total
  equity here, exactly as ``ledger.shares(sid) * prices.get(sid, 0.0)`` does in the
  engine — never a fabricated non-zero mark. It is separately reported in
  ``missing_marks`` so a caller can see WHY a weight is under-covered, rather than
  guessing that it's simply zero-weighted.

Ordering per date, matching the engine's own event order for that date: corporate
actions first (they act on shares held as of the prior close), then fills, then the
end-of-day mark. Same-day actions/fills across different stocks commute (each stock's
ledger is independent), so within a date this module applies them sorted by
``stock_id`` only for determinism, not because engine order matters there.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

EXACT_LEDGER_CONTRACT_VERSION = "portfolio-ledger/h0/1"

DAILY_ROW_FIELDS = (
    "date", "cash", "shares_by_stock", "mtm_value_by_stock", "stock_weight_by_stock",
    "cash_weight", "total_equity", "daily_return", "missing_marks",
)


def fill_cash_delta(fill: Mapping) -> float:
    """Exact cash effect of one stored fill. Uses only ``fill_price`` (the actual
    executed price, slippage included) and the stored ``commission``/``tax`` — never
    ``reference_price``/``reference_notional``, which are the pre-slippage quote and
    would silently drift from the engine's real cash flow when slippage is nonzero."""
    shares = float(fill["shares"])
    price = float(fill["fill_price"])
    commission = float(fill.get("commission") or 0.0)
    notional = shares * price
    if fill["side"] == "buy":
        return -(notional + commission)
    if fill["side"] == "sell":
        tax = float(fill.get("tax") or 0.0)
        return notional - commission - tax
    raise ValueError(f"unknown fill side: {fill.get('side')!r}")


def replay_sleeve(dates: Sequence[str], fills: Sequence[Mapping], actions: Sequence[Mapping],
                  closes: Mapping[str, Mapping[str, float]], initial_cash: float) -> list[dict]:
    """Exact per-date ledger for one evaluated fold.

    ``dates``: the fold's OOS trading dates (sorted), normally the same dates as the
    stored ``walkforward_equity`` rows for that fold — this module does not invent a
    trading calendar.
    ``fills``: fold-scoped 'fill' trades (date, stock_id, side, shares, fill_price,
    commission, tax).
    ``actions``: corporate_actions rows restricted to the fold's test window (date,
    stock_id, cash_dividend, share_multiplier) — rows outside the window cannot affect
    an independent fold's ledger because it starts fresh from cash at test_start.
    ``closes``: ``{stock_id: {date: close}}``, only dates where a quote exists.
    """
    fills_by_date: dict[str, list[Mapping]] = defaultdict(list)
    for f in fills:
        fills_by_date[f["date"]].append(f)
    actions_by_date: dict[str, list[Mapping]] = defaultdict(list)
    for a in actions:
        actions_by_date[a["date"]].append(a)

    shares: dict[str, float] = {}
    cash = float(initial_cash)
    last_close: dict[str, float] = {}
    prev_equity = float(initial_cash)
    rows: list[dict] = []

    for d in dates:
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

        for f in sorted(fills_by_date.get(d, ()), key=lambda x: x["stock_id"]):
            sid = f["stock_id"]
            delta = float(f["shares"]) if f["side"] == "buy" else -float(f["shares"])
            shares[sid] = shares.get(sid, 0.0) + delta
            if abs(shares[sid]) < 1e-9:
                shares.pop(sid, None)
            cash += fill_cash_delta(f)

        for sid in shares:
            px = closes.get(sid, {}).get(d)
            if px is not None:
                last_close[sid] = px

        mtm_value: dict[str, float | None] = {}
        for sid in shares:
            px = last_close.get(sid)
            mtm_value[sid] = shares[sid] * px if px is not None else None
        missing_marks = sorted(sid for sid, v in mtm_value.items() if v is None)
        # Matches engine._equity(): a position with no observed price ever contributes
        # 0.0, it is not excluded from the ledger, only from the marked total.
        total_stock_value = sum(v for v in mtm_value.values() if v is not None)
        total_equity = cash + total_stock_value
        stock_weight = {
            sid: (v / total_equity if v is not None and total_equity else (None if v is None else 0.0))
            for sid, v in mtm_value.items()
        }
        cash_weight = cash / total_equity if total_equity else None
        daily_return = (total_equity / prev_equity - 1.0) if prev_equity else None

        rows.append({
            "date": d, "cash": cash,
            "shares_by_stock": dict(shares),
            "mtm_value_by_stock": mtm_value,
            "stock_weight_by_stock": stock_weight,
            "cash_weight": cash_weight,
            "total_equity": total_equity,
            "daily_return": daily_return,
            "missing_marks": missing_marks,
        })
        prev_equity = total_equity

    return rows
