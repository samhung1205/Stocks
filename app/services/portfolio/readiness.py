"""Stage H0 Task 5: allocation-method capability contract.

For each candidate allocation method, decides whether the CURRENT evidence supports
running it at all — never computes an allocation. A method with insufficient evidence
reports ``insufficient_evidence`` / ``unavailable``; it never falls back to a fabricated
or imputed number. Every caller is expected to have already routed its dated inputs
through ``leakage.prior_only_returns`` — this module only judges sample size /
coverage, it does not itself know "now".
"""
from __future__ import annotations

import statistics
from typing import Mapping, Sequence

READINESS_CONTRACT_VERSION = "portfolio-allocation-readiness/h0/1"

# Contract floors, not statistical-adequacy claims (explicitly, per the stage
# instructions). MIN_PAIRWISE_OVERLAP_DAYS reuses the exact floor Stage G's own
# correlation module already established (``cross_strategy/correlation.py``:
# "pairwise-complete, minimum 5 overlapping trading days") so this contract does not
# invent a second, inconsistent number for the same underlying question.
MIN_PAIRWISE_OVERLAP_DAYS = 5
MIN_VOL_SAMPLE_DAYS = 20

_COVARIANCE_METHODS = ("risk_parity", "minimum_variance", "correlation_aware")


def equal_weight_readiness(sleeve_keys: Sequence[str]) -> dict:
    ready = len(sleeve_keys) >= 1
    return {"method": "equal_weight", "status": "ready" if ready else "unavailable",
            "reason": None if ready else "no sleeves", "sleeve_count": len(sleeve_keys)}


def inverse_volatility_readiness(prior_returns_by_strategy: Mapping[str, Sequence[float]]) -> dict:
    """``prior_returns_by_strategy`` must already be filtered by the caller (via
    ``leakage.prior_only_returns``) to dates strictly before the allocation date — this
    function only checks whether the resulting sample is large enough, per strategy."""
    per_strategy = {}
    for strat, returns in prior_returns_by_strategy.items():
        n = len(returns)
        if n >= MIN_VOL_SAMPLE_DAYS:
            per_strategy[strat] = {"status": "ready", "n": n, "vol": statistics.pstdev(returns)}
        else:
            per_strategy[strat] = {"status": "insufficient_evidence", "n": n, "vol": None}
    if not per_strategy:
        overall = "unavailable"
    elif all(v["status"] == "ready" for v in per_strategy.values()):
        overall = "ready"
    else:
        overall = "insufficient_evidence"
    return {"method": "inverse_volatility", "status": overall, "min_sample_days": MIN_VOL_SAMPLE_DAYS,
            "per_strategy": per_strategy}


def _pairs(keys: Sequence[str]) -> list[tuple[str, str]]:
    ordered = sorted(keys)
    return [(ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered))]


def covariance_readiness(method: str, overlap_days_by_pair: Mapping[tuple[str, str], int],
                         sleeve_keys: Sequence[str]) -> dict:
    """``risk_parity`` / ``minimum_variance`` / ``correlation_aware`` all need a FULL
    pairwise covariance/correlation matrix over ``sleeve_keys``. Never fills a missing
    or thin pair with an imputed number — if ANY required pair is below the minimum
    overlap (or has zero overlap, as several Stage G pairs genuinely do — LVQ never
    shared an evaluated fold with QGM or RGA), the whole method reports not ready,
    with the offending pair(s) named."""
    if method not in _COVARIANCE_METHODS:
        raise ValueError(f"unknown covariance-dependent method: {method}")
    if len(sleeve_keys) < 2:
        return {"method": method, "status": "unavailable", "min_overlap_days": MIN_PAIRWISE_OVERLAP_DAYS,
                "pairs": {}, "reason": "fewer than 2 sleeves"}
    per_pair = {}
    worst = "ready"
    for a, b in _pairs(sleeve_keys):
        days = overlap_days_by_pair.get((a, b))
        if days is None:
            days = overlap_days_by_pair.get((b, a), 0)
        if days <= 0:
            per_pair[f"{a}|{b}"] = {"status": "unavailable", "overlap_days": 0}
            worst = "unavailable"
        elif days < MIN_PAIRWISE_OVERLAP_DAYS:
            per_pair[f"{a}|{b}"] = {"status": "insufficient_evidence", "overlap_days": days}
            if worst == "ready":
                worst = "insufficient_evidence"
        else:
            per_pair[f"{a}|{b}"] = {"status": "ready", "overlap_days": days}
    return {"method": method, "status": worst, "min_overlap_days": MIN_PAIRWISE_OVERLAP_DAYS, "pairs": per_pair}


def assess_all(sleeve_keys: Sequence[str], prior_returns_by_strategy: Mapping[str, Sequence[float]],
              overlap_days_by_pair: Mapping[tuple[str, str], int]) -> dict:
    return {
        "contract_version": READINESS_CONTRACT_VERSION,
        "equal_weight": equal_weight_readiness(sleeve_keys),
        "inverse_volatility": inverse_volatility_readiness(prior_returns_by_strategy),
        "risk_parity": covariance_readiness("risk_parity", overlap_days_by_pair, sleeve_keys),
        "minimum_variance": covariance_readiness("minimum_variance", overlap_days_by_pair, sleeve_keys),
        "correlation_aware": covariance_readiness("correlation_aware", overlap_days_by_pair, sleeve_keys),
    }
