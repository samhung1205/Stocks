"""Stage H1: rebalance-date selection from a versioned rebalance config.

Only ``monthly`` (the first common trading date of each calendar month) is
implemented in Stage H1 — any other frequency is rejected explicitly at
definition time rather than silently defaulting to something else.
"""
from __future__ import annotations

from typing import Sequence

SUPPORTED_FREQUENCIES = ("monthly",)


def rebalance_dates(dates: Sequence[str], frequency: str) -> list[str]:
    if frequency not in SUPPORTED_FREQUENCIES:
        raise NotImplementedError(f"rebalance frequency {frequency!r} is not implemented in Stage H1; "
                                  f"supported: {SUPPORTED_FREQUENCIES}")
    seen: set[str] = set()
    out: list[str] = []
    for d in dates:
        ym = d[:7]
        if ym not in seen:
            seen.add(ym)
            out.append(d)
    return out
