"""Price-only adjustment series for FACTOR computation (momentum, MA, volatility).

This is deliberately separate from portfolio accounting: the engine trades and
marks positions at raw open/close and handles cash/share changes through
``CorporateAction.cash_dividend`` / ``share_multiplier``.  ``adj_close`` is never
used for execution, fills or equity.

Convention (start-anchored, forward accumulation, point-in-time safe)
---------------------------------------------------------------------
Each event carries the exchange reference-price ratio ``r = after/before``
(reference price on the ex-date over the previous close).  For a stock

    C(t) = product of r_i over events with ex_date <= t          (C = 1 before any event)
    adj_close(t) = close(t) / C(t)

so the ex-date's adjusted return is ``close_t / (r * close_{t-1}) - 1``, i.e. the
market move relative to the reference price, with no artificial gap.  Level at
date t depends only on events dated <= t: later events never rewrite history,
so a signal on date T sees exactly what it would have seen live.  Only ratios
(momentum, MA distance, volatility) are meaningful -- the level is not the
quoted price.

Because ``r`` already contains cash and stock components of one event, the
factor is total-return-like (cash dividends are removed from the ex-date drop).
Exactly ONE factor is applied per (stock, ex-date): duplicate rows for the same
day (e.g. dividend-result and split-price both describing one reference-price
change) are collapsed, preferring split/reverse-split rows, never multiplied.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from typing import Iterable, Mapping

from .types import Bar

MIN_RATIO, MAX_RATIO = 0.01, 100.0
RESIDUAL_JUMP = 0.25  # |adjusted return| beyond this on an ex-date => data suspect
_SPLIT_KINDS = ("split", "reverse_split")


def event_ratios(actions: Iterable[Mapping]) -> tuple[dict[tuple[str, str], float], dict]:
    """One reference-price ratio per (stock_id, date) plus a small diagnostics dict."""
    grouped: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    stats = {"events": 0, "invalid_ratio": 0, "same_day_collapsed": 0}
    for action in actions:
        get = action.get if isinstance(action, Mapping) else lambda k, d=None: getattr(action, k, d)
        ratio = get("ratio")
        if ratio is None or not math.isfinite(ratio) or not MIN_RATIO <= ratio <= MAX_RATIO:
            stats["invalid_ratio"] += ratio is not None
            continue
        grouped[(get("stock_id"), get("date"))].append((get("kind") or "", float(ratio)))
    out = {}
    for key, rows in grouped.items():
        if len(rows) > 1:
            stats["same_day_collapsed"] += 1
            rows = sorted(rows, key=lambda r: (r[0] not in _SPLIT_KINDS, r[0], r[1]))
        out[key] = rows[0][1]
    stats["events"] = len(out)
    return out, stats


def adjust_bars(bars_by_date: Mapping[str, Mapping[str, Bar]], actions: Iterable[Mapping],
                dates: Iterable[str]) -> tuple[dict[str, dict[str, Bar]], dict]:
    """Return bars with ``adj_close`` filled in, and diagnostics (incl. residual jumps)."""
    ratios, stats = event_ratios(actions)
    by_stock: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (stock_id, date), ratio in ratios.items():
        by_stock[stock_id].append((date, ratio))
    for events in by_stock.values():
        events.sort()
    cum = {sid: 1.0 for sid in by_stock}
    nxt = {sid: 0 for sid in by_stock}
    prev_adj: dict[str, float] = {}
    residual = []
    out: dict[str, dict[str, Bar]] = {}
    for date in sorted(dates):
        day = bars_by_date.get(date, {})
        # Events on non-session days take effect from the next bar (ex-date <= t).
        for sid, events in by_stock.items():
            while nxt[sid] < len(events) and events[nxt[sid]][0] <= date:
                cum[sid] *= events[nxt[sid]][1]
                nxt[sid] += 1
        new_day = {}
        for sid, bar in day.items():
            close = bar.close
            if close is not None and close > 0:
                adj = close / cum.get(sid, 1.0)
                if (sid, date) in ratios and sid in prev_adj and prev_adj[sid] > 0:
                    jump = adj / prev_adj[sid] - 1
                    if abs(jump) > RESIDUAL_JUMP:
                        residual.append((sid, date, round(jump, 4)))
                prev_adj[sid] = adj
                new_day[sid] = replace(bar, adj_close=adj)
            else:
                new_day[sid] = bar
        out[date] = new_day
    stats["residual_jumps"] = residual
    return out, stats
