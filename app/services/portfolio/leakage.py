"""Stage H0 Task 7: point-in-time / no-future-leakage guards.

A future portfolio allocation decision made "as of" date T may use sleeve candidate
status, historical sleeve returns, risk/covariance estimates and holdings that were
knowable AT or BEFORE T — never a later OOS fold's outcome. This module is the shared
guard other Stage H0 modules (``readiness.py``) are built to be used through, and the
thing Stage H1's allocation engine must route every dated input through.
"""
from __future__ import annotations

from typing import Mapping, Sequence


class LeakageError(ValueError):
    pass


def assert_point_in_time(as_of: str, dated_inputs: Mapping[str, str] | Sequence[tuple[str, str]]) -> None:
    """``dated_inputs``: ``{label: date}`` (or an iterable of ``(label, date)`` pairs) for
    every input that fed an allocation decision made as of ``as_of``. Raises
    ``LeakageError`` naming the first offending input if any date is strictly after
    ``as_of``. A ``None`` date (unknown/not applicable) is not checked."""
    items = dated_inputs.items() if isinstance(dated_inputs, Mapping) else dated_inputs
    for label, date in items:
        if date is not None and date > as_of:
            raise LeakageError(f"{label}: input dated {date} is after allocation date {as_of}")


def prior_only_returns(returns_by_date: Mapping[str, float], as_of: str) -> dict[str, float]:
    """Filter a ``{date: return}`` series to strictly-prior dates (``date < as_of``) —
    the building block ``readiness.inverse_volatility_readiness`` (and any future
    covariance estimator) must be fed through, so a later OOS fold's results can never
    influence an earlier allocation decision. Always returns a NEW dict, so later
    mutation of the caller's source series cannot retroactively change an
    already-computed result."""
    return {d: r for d, r in returns_by_date.items() if d < as_of}
