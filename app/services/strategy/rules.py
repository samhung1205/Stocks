"""Generic scheduling and position-sizing helpers (no strategy templates)."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .types import PositionSizingRule, RebalanceRule, Signal


def is_rebalance_day(dates: Sequence[str], index: int, rule: RebalanceRule) -> bool:
    date = dates[index]
    if rule.frequency == "daily":
        return True
    if rule.frequency == "dates":
        return date in rule.dates
    current = date[:7] if rule.frequency == "monthly" else _iso_week(date)
    if index == len(dates) - 1:
        return True
    following = dates[index + 1][:7] if rule.frequency == "monthly" else _iso_week(dates[index + 1])
    return current != following


def _iso_week(value: str) -> tuple[int, int]:
    import datetime as dt
    iso = dt.date.fromisoformat(value).isocalendar()
    return iso.year, iso.week


def target_weights(value, rule: PositionSizingRule) -> dict[str, float]:
    """Convert a Signal/mapping/list to a long-only target portfolio."""
    signal = value if isinstance(value, Signal) else (
        Signal(targets=value) if isinstance(value, Mapping)
        else Signal(candidates=tuple(value or ())))
    if signal.targets is not None and rule.method == "signal_weights":
        raw = {str(k): float(v) for k, v in signal.targets.items() if float(v) > 0}
    else:
        ids = list(dict.fromkeys(str(x) for x in signal.candidates))
        if not ids and signal.targets:
            ids = list(signal.targets)
        if not ids:
            return {}
        if rule.method == "score_weighted":
            positive = {sid: max(float(signal.scores.get(sid, 0)), 0.0) for sid in ids}
            total = sum(positive.values())
            raw = ({sid: score / total for sid, score in positive.items()}
                   if total else {sid: 1 / len(ids) for sid in ids})
        else:
            raw = {sid: 1 / len(ids) for sid in ids}
    if any(not math.isfinite(v) for v in raw.values()):
        raise ValueError("target weights must be finite")
    investable = 1 - rule.cash_weight
    total = sum(raw.values())
    if total <= 0:
        return {}
    # Preserve an intentional cash allocation in supplied weights, but scale
    # down portfolios that exceed the investable budget.
    scale = min(1.0, investable / total)
    capped = {sid: min(weight * scale, rule.max_position_weight)
              for sid, weight in raw.items()}
    return capped


def static_signals(by_date: Mapping[str, Signal | Mapping[str, float] | Sequence[str]]):
    """Return a signal callable useful for deterministic research/tests."""
    return lambda context: by_date.get(context.date)
