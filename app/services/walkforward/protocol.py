"""Walk-forward protocol defaults and pure fold generation (no DB, no engine).

Defaults are a *reasonable research choice for ~3.9 years of data*
(2022-11-01 .. 2026-09-18), not a statistically optimal design:

* expanding window – rolling would throw away scarce early data and make every
  fold's optimisation depend on a shorter, noisier sample.
* 24 months initial training – long enough to span at least one full market
  regime change and the lookbacks used (<= 252 sessions come from warm-up data
  before ``data_start``, which may feed signals but never metrics).
* 6 months test, 6 months step – non-overlapping tests, 3 full folds + 1 shorter
  final fold (kept only if >= 3 months).  More/shorter folds would mean
  re-optimising on almost identical data and evaluating on a few trades.
* gap = 0 sessions – there are no forward labels, so nothing overlaps the
  boundary; signals on the last training day execute at T+1 inside the test
  window under the *frozen* candidate.  ``gap_sessions`` exists for experiments
  but is not the default: an embargo would burn ~1/12 of the sample for no leak.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Mapping, Sequence

PROTOCOL_VERSION = "wf-protocol/f0.5/1"
DEFAULT_PROTOCOL: dict = {
    "mode": "expanding",             # expanding | rolling
    "initial_train_months": 24,
    "rolling_train_months": None,    # required only when mode == rolling
    "test_months": 6,
    "step_months": 6,                # re-optimise once per fold
    "min_final_test_months": 3,
    "gap_sessions": 0,
    "reoptimize": "each_fold",
    "candidate_rule": "wf-freeze/f0.5/2",
    "execution_policy": "partial",   # missing.execution used for WF backtests (see F0 report)
}


def add_months(iso: str, months: int) -> str:
    d = dt.date.fromisoformat(iso)
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    day = min(d.day, calendar.monthrange(y, m + 1)[1])
    return dt.date(y, m + 1, day).isoformat()


def validate(protocol: Mapping | None) -> dict:
    p = {**DEFAULT_PROTOCOL, **(protocol or {})}
    unknown = set(p) - set(DEFAULT_PROTOCOL)
    if unknown:
        raise ValueError(f"unknown protocol key: {sorted(unknown)[0]}")
    if p["mode"] not in ("expanding", "rolling"):
        raise ValueError("mode must be expanding or rolling")
    for k in ("initial_train_months", "test_months", "step_months", "min_final_test_months"):
        if isinstance(p[k], bool) or not isinstance(p[k], int) or p[k] < 1:
            raise ValueError(f"{k} must be a positive integer")
    if p["mode"] == "rolling" and not (isinstance(p["rolling_train_months"], int)
                                       and p["rolling_train_months"] >= 1):
        raise ValueError("rolling mode needs rolling_train_months")
    if p["step_months"] < p["test_months"]:
        raise ValueError("step_months must be >= test_months (overlapping test windows double-count)")
    if not isinstance(p["gap_sessions"], int) or isinstance(p["gap_sessions"], bool) or p["gap_sessions"] < 0:
        raise ValueError("gap_sessions must be a non-negative integer")
    if p["min_final_test_months"] > p["test_months"]:
        raise ValueError("min_final_test_months must be <= test_months")
    if p["execution_policy"] not in ("next_available", "cancel", "partial"):
        raise ValueError("execution_policy must be next_available, cancel or partial")
    return p


def generate_folds(data_start: str, data_end: str, sessions: Sequence[str],
                   protocol: Mapping | None = None) -> dict:
    """Fold boundaries snapped to trading ``sessions`` (ascending ISO dates).

    Train windows end strictly before the test window (minus ``gap_sessions``);
    test windows never overlap; nothing extends past ``data_end``.
    """
    p = validate(protocol)
    days = sorted(d for d in sessions if data_start <= d <= data_end)
    if not days:
        raise ValueError("no trading sessions in [data_start, data_end]")
    folds, dropped, notes = [], [], []
    boundary = add_months(data_start, p["initial_train_months"])
    while True:
        first_idx = next((i for i, d in enumerate(days) if d >= boundary), None)
        if first_idx is None:
            break
        nominal_end = add_months(boundary, p["test_months"])          # exclusive
        last_idx = max((i for i, d in enumerate(days) if d < nominal_end), default=None)
        truncated = nominal_end > (dt.date.fromisoformat(data_end) + dt.timedelta(days=1)).isoformat()
        train_end_idx = first_idx - 1 - p["gap_sessions"]
        if train_end_idx < 0 or last_idx is None or last_idx < first_idx:
            break
        train_start = data_start if p["mode"] == "expanding" else \
            next((d for d in days if d >= add_months(boundary, -p["rolling_train_months"])), days[0])
        test_days = (dt.date.fromisoformat(days[last_idx]) - dt.date.fromisoformat(days[first_idx])).days + 1
        fold = {"index": len(folds), "train_start": max(train_start, days[0]),
                "train_end": days[train_end_idx], "test_start": days[first_idx],
                "test_end": days[last_idx], "gap_sessions": p["gap_sessions"],
                "test_sessions": last_idx - first_idx + 1, "truncated": truncated,
                "test_months_approx": round(test_days / 30.4, 1)}
        if truncated and fold["test_months_approx"] + 0.5 < p["min_final_test_months"]:
            dropped.append({**fold, "reason": "final test window shorter than min_final_test_months"})
            break
        folds.append(fold)
        boundary = add_months(boundary, p["step_months"])
    if len(folds) < 2:
        notes.append("FEWER_THAN_2_FOLDS")
    if any(f["truncated"] for f in folds):
        notes.append("PARTIAL_FOLD_TEST_WINDOW")
    return {"protocol_version": PROTOCOL_VERSION, "protocol": p, "data_start": data_start,
            "data_end": data_end, "folds": folds, "dropped": dropped, "notes": notes}
