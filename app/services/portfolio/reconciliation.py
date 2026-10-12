"""Stage H0 Task 1: reconcile the exact ledger against the stored independent OOS.

Per the stage instructions: a mismatch is a CORRECTNESS ISSUE, never something to
normalize away. This module only compares and reports; it never adjusts either side
to make them agree.
"""
from __future__ import annotations

from typing import Mapping, Sequence

# Both ledgers are built from the exact same stored numbers (fill_price, commission,
# tax, share counts) with no re-derived rounding, so a true match should be exact to
# floating-point summation error. This tolerance is generous only for that noise, not
# for any modeling difference.
ABS_TOLERANCE = 1e-6
REL_TOLERANCE = 1e-9


def _close_enough(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(ABS_TOLERANCE, abs(expected) * REL_TOLERANCE)


def reconcile(stored_equity: Sequence[Mapping], daily_rows: Sequence[Mapping]) -> dict:
    """``stored_equity``: the fold's ``walkforward_equity`` points (date, net_equity).
    ``daily_rows``: this module's own ``ledger.replay_sleeve`` output (or the flat
    no_candidate equivalent). Returns a report; ``reconciled`` is False if ANY date
    mismatches or is missing on either side — the caller must treat that as a
    correctness issue to investigate, not silently accept."""
    stored_by_date = {p["date"]: p["net_equity"] for p in stored_equity}
    exact_by_date = {r["date"]: r["total_equity"] for r in daily_rows}
    all_dates = sorted(set(stored_by_date) | set(exact_by_date))
    mismatches = []
    for d in all_dates:
        expected = stored_by_date.get(d)
        actual = exact_by_date.get(d)
        if expected is None or actual is None:
            mismatches.append({"date": d, "expected": expected, "actual": actual, "reason": "missing_on_one_side"})
            continue
        if not _close_enough(actual, expected):
            mismatches.append({"date": d, "expected": expected, "actual": actual,
                               "diff": actual - expected, "reason": "value_mismatch"})
    return {
        "dates_checked": len(all_dates),
        "mismatches": mismatches,
        "reconciled": not mismatches,
        "abs_tolerance": ABS_TOLERANCE, "rel_tolerance": REL_TOLERANCE,
    }
