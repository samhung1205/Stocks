"""Stage F0: stable backend warning severity (info / caution / critical).

Pure.  No risk score: every warning maps to exactly one severity through an
ordered pattern table, and the *code* is what later stages/UI should key on
(message wording may change; codes may not).  A message no pattern recognises
is ``UNCLASSIFIED`` / ``caution`` — never silently ``info``.

* info     – context the reader should know; results remain usable as stated.
* caution  – the result is usable only after reading the warning (data
             substitution, heuristic fallback, boundary or coverage limits).
* critical – the affected number (P&L, holding period, or fold) is unreliable
             or an intended trade did not happen; must not be shown as a clean result.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

CONTRACT_VERSION = "warning-severity/f0/1"
SEVERITIES = ("info", "caution", "critical")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# (regex, code, severity) — first match wins, so specific patterns come first.
ENGINE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"UNRESOLVED_CORPORATE_ACTION", "UNRESOLVED_CORPORATE_ACTION", "critical"),
    (r"delisting exit unavailable", "DELISTING_EXIT_UNAVAILABLE", "critical"),
    (r"invalid share_multiplier ignored", "INVALID_SHARE_MULTIPLIER", "critical"),
    (r"no valuation price", "NO_VALUATION_PRICE", "critical"),
    (r"execution price missing.*; cancelled", "EXECUTION_CANCELLED", "critical"),
    (r"PARTIAL_REBALANCE", "PARTIAL_REBALANCE", "caution"),
    (r"execution price missing.*; deferred", "EXECUTION_DEFERRED", "caution"),
    (r"newer signal replaced unfilled target order", "TARGET_ORDER_REPLACED", "caution"),
    (r"closing price missing; execution price used", "VALUATION_USED_EXECUTION_PRICE", "caution"),
    (r"valuation is \d+ sessions stale", "STALE_VALUATION", "caution"),
    (r"adjusted-price residual jump", "ADJUSTED_PRICE_RESIDUAL_JUMP", "caution"),
    (r"sector-neutral fallback", "SECTOR_NEUTRAL_FALLBACK", "caution"),
    (r"assumed publication mode", "ASSUMED_PUBLICATION_MODE", "caution"),
    (r"trading_calendar missing", "TRADING_CALENDAR_MISSING", "caution"),
    (r"signal falls beyond backtest execution window", "SIGNAL_BEYOND_WINDOW", "info"),
)

# Structural (non-string) findings produced by optimization / walk-forward analysis.
STRUCTURAL: dict[str, str] = {
    "IN_SAMPLE_ONLY": "info",
    "SAMPLED_GRID": "info",
    "INSUFFICIENT_ROBUSTNESS_DATA": "info",
    "EDGE_OF_SEARCH_RANGE": "caution",
    "ISOLATED_PEAK": "caution",
    "SCATTERED_SURFACE": "caution",
    "FLAT_SURFACE": "caution",
    "FROZEN_WITHOUT_PLATEAU": "caution",
    "EMPTY_SIGNALS": "caution",
    "PARTIAL_FOLD_TEST_WINDOW": "caution",
    "REVISION_WITHOUT_EDGE": "caution",
    "PARTIAL_REBALANCE_USED": "caution",
    "FAILED_RUNS": "caution",
    "ALL_SIGNALS_EMPTY": "critical",
    "UNRESOLVED_ACTIONS_IN_WINDOW": "critical",
    "NO_STABLE_CANDIDATE": "critical",
    "LEGACY_JOB_NO_PROVENANCE": "info",
    # Stage F1 (walk forward)
    "PRIMARY_ON_SEARCH_EDGE": "caution",
    "FOLD_HELD_CASH": "caution",
    "STITCHED_LIQUIDATION_AT_NO_CANDIDATE": "caution",
    "STITCHED_NOT_INDEPENDENT_EVIDENCE": "info",
    "FOLD_DISPERSION_DESCRIPTIVE": "info",
    # Stage G (cross-strategy analysis)
    "CROSS_STRATEGY_DESCRIPTIVE_ONLY": "info",
    "DEGENERATE_CASH_SERIES_EXCLUDED": "info",
    "FOLD_WINDOW_MISALIGNED": "caution",
    "CURRENT_SECTOR_TAXONOMY_ONLY": "caution",
    "SPARSE_OVERLAP_EVIDENCE": "info",
    "MISSING_STRATEGY_DATA": "caution",
    "ALL_STRATEGIES_NO_CANDIDATE": "caution",
}

_COMPILED = tuple((re.compile(p), code, sev) for p, code, sev in ENGINE_PATTERNS)


def classify(message: str) -> dict:
    """Map one engine/panel/signal warning string to ``{code, severity, message}``."""
    text = str(message)
    for pattern, code, severity in _COMPILED:
        if pattern.search(text):
            return {"code": code, "severity": severity, "message": text}
    return {"code": "UNCLASSIFIED", "severity": "caution", "message": text}


def structural(code: str, detail: str | None = None) -> dict:
    if code not in STRUCTURAL:
        raise KeyError(f"unknown structural warning code: {code}")
    return {"code": code, "severity": STRUCTURAL[code], "message": detail or code}


def max_severity(severities: Iterable[str]) -> str | None:
    best = None
    for s in severities:
        if s not in _RANK:
            raise ValueError(f"unknown severity: {s}")
        if best is None or _RANK[s] > _RANK[best]:
            best = s
    return best


def summarize(items: Iterable[Mapping]) -> dict:
    """Counts per severity, per code (worst-first), and the overall maximum."""
    items = list(items)
    counts = {s: 0 for s in SEVERITIES}
    by_code: dict[str, dict] = {}
    for it in items:
        counts[it["severity"]] += 1
        entry = by_code.setdefault(it["code"], {"code": it["code"], "severity": it["severity"], "count": 0})
        entry["count"] += 1
    ordered = sorted(by_code.values(), key=lambda e: (-_RANK[e["severity"]], e["code"]))
    return {"contract": CONTRACT_VERSION, "max_severity": max_severity(i["severity"] for i in items),
            "counts": counts, "codes": ordered}


def classify_all(messages: Iterable[str]) -> dict:
    items = [classify(m) for m in dict.fromkeys(str(m) for m in messages)]
    return {"items": items, **summarize(items)}


def catalog() -> dict:
    return {"contract": CONTRACT_VERSION, "severities": list(SEVERITIES),
            "unclassified_default": "caution",
            "engine_patterns": [{"pattern": p, "code": c, "severity": s} for p, c, s in ENGINE_PATTERNS],
            "structural": [{"code": c, "severity": s} for c, s in STRUCTURAL.items()]}
