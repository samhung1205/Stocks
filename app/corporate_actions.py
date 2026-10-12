"""Corporate-action classification: pure, derived only from source fields.

FinMind ``TaiwanStockDividendResult`` labels the event with either the short
(息/權/權息) or the long (除息/除權/除權息) spelling, and reports
``stock_and_cache_dividend`` = *before_price minus the unrounded exchange
reference price* (verified on every stored row: |before - value - after| is
within rounding of the 0.01 reference price).  Consequences:

* pure cash event  -> that value IS the cash dividend per share.
* pure stock event -> reference = before / m, so m = before / (before - value),
  a unique derivation of the share multiplier (no cash component to confuse it).
* mixed event      -> one equation, two unknowns (cash and stock rate): NOT
  separable from this source.  Left unresolved; never guessed.

``TaiwanStockSplitPrice`` 面額變更 (par-value change) carries exchange reference
before/after prices with no cash; the multiplier is the unique half-integer (or
1/k) consistent with the rounded reference price.  Other split types stay
unresolved.

``ratio`` (after/before) remains a reference-price ratio: it is used only for
the price-only adjustment series, never as a share multiplier.
"""
from __future__ import annotations

import json
import math

CASH_LABELS = frozenset({"息", "除息"})
STOCK_LABELS = frozenset({"權", "除權"})
MIXED_LABELS = frozenset({"權息", "除權息"})
PURE_SPLIT_TYPES = frozenset({"面額變更"})

# Reference price is rounded to 0.01 and the value has ~6 decimals; TWSE-style
# rows show up to ~0.0095 of residual, so 0.0101 is the rounding bound.
VALUE_TOLERANCE = 0.0101
SPLIT_TOLERANCE = 0.00501  # reference price rounded half-up to 0.01

# Resolution codes (stored in corporate_actions.resolution).
CASH_VERIFIED = "cash_verified"
STOCK_MULTIPLIER = "stock_multiplier_derived"
SPLIT_MULTIPLIER = "split_multiplier_derived"
UNRESOLVED_MIXED = "unresolved_mixed"
UNRESOLVED_VALUE = "unresolved_invalid_value"
UNRESOLVED_LABEL = "unresolved_label"
UNRESOLVED_SPLIT = "unresolved_split"
UNRESOLVED = frozenset({UNRESOLVED_MIXED, UNRESOLVED_VALUE, UNRESOLVED_LABEL, UNRESOLVED_SPLIT})


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def snap_split_multiplier(before: float, after: float) -> float | None:
    """Unique n/2 (n>=3) or 1/k multiplier consistent with the rounded reference."""
    if not (before and after and before > 0 and after > 0):
        return None
    m = before / after
    candidates = set()
    if m > 1:
        centre = int(round(m * 2))
        candidates.update(k / 2 for k in range(max(3, centre - 3), centre + 4))
    else:
        centre = int(round(after / before))
        candidates.update(1 / k for k in range(max(2, centre - 2), centre + 3))
    ok = [c for c in candidates if abs(before / c - after) <= SPLIT_TOLERANCE]
    return ok[0] if len(ok) == 1 else None


def classify(source: str, raw: dict) -> dict:
    """Return kind/cash_dividend/share_multiplier/resolution/ratio for one raw row."""
    before, after = _num(raw.get("before_price")), _num(raw.get("after_price"))
    ratio = after / before if before and after and before > 0 else None
    out = {"kind": "unknown", "cash_dividend": None, "share_multiplier": None,
           "resolution": UNRESOLVED_LABEL, "ratio": ratio}
    if source.endswith("TaiwanStockSplitPrice"):
        out["kind"] = ("split" if after < before else "reverse_split") if before and after and before != after \
            else "unknown"
        if raw.get("type") in PURE_SPLIT_TYPES and before and after:
            m = snap_split_multiplier(before, after)
            if m:
                out.update(share_multiplier=m, resolution=SPLIT_MULTIPLIER)
                return out
        out["resolution"] = UNRESOLVED_SPLIT
        return out

    label = raw.get("stock_or_cache_dividend")
    value = _num(raw.get("stock_and_cache_dividend"))
    if label in CASH_LABELS:
        out["kind"] = "dividend_cash"
    elif label in STOCK_LABELS:
        out["kind"] = "dividend_stock"
    elif label in MIXED_LABELS:
        out["kind"] = "dividend_mixed"
        out["resolution"] = UNRESOLVED_MIXED
        return out
    else:
        return out
    consistent = (value is not None and value > 0 and before and after and before > 0
                  and after > 0 and value < before
                  and abs((before - value) - after) <= VALUE_TOLERANCE)
    if not consistent:
        out["resolution"] = UNRESOLVED_VALUE
    elif out["kind"] == "dividend_cash":
        out.update(cash_dividend=value, resolution=CASH_VERIFIED)
    else:
        out.update(share_multiplier=before / (before - value), resolution=STOCK_MULTIPLIER)
    return out


def fields_for(source: str, raw: dict) -> dict:
    """Row columns derived from a raw source record (used by ingest and repair)."""
    c = classify(source, raw)
    return {"kind": c["kind"], "cash_dividend": c["cash_dividend"], "stock_dividend": None,
            "share_multiplier": c["share_multiplier"], "resolution": c["resolution"],
            "ratio": c["ratio"]}


def repair(conn) -> dict:
    """Re-derive kind/cash/multiplier/resolution from stored raw_json. Idempotent.

    Works on a SQLAlchemy Connection; no network.  Rows whose derived fields
    already match are untouched, so a second run reports zero changes.
    """
    rows = conn.exec_driver_sql(
        "SELECT stock_id,date,kind,source,cash_dividend,stock_dividend,share_multiplier,"
        "resolution,raw_json FROM corporate_actions").fetchall()
    updates, changed_kind = [], 0
    for sid, date, kind, source, cash, stock, mult, res, raw_json in rows:
        try:
            raw = json.loads(raw_json)
        except (TypeError, ValueError):
            continue
        new = fields_for(source, raw)
        same = (new["kind"] == kind and _eq(new["cash_dividend"], cash)
                and _eq(new["share_multiplier"], mult) and new["resolution"] == res
                and stock is None)
        if same:
            continue
        changed_kind += new["kind"] != kind
        updates.append((new["kind"], new["cash_dividend"], None, new["share_multiplier"],
                        new["resolution"], sid, date, kind, source))
    if updates:
        conn.exec_driver_sql(
            "UPDATE corporate_actions SET kind=?,cash_dividend=?,stock_dividend=?,"
            "share_multiplier=?,resolution=? WHERE stock_id=? AND date=? AND kind=? AND source=?",
            updates)
    counts = dict(conn.exec_driver_sql(
        "SELECT COALESCE(resolution,'(none)'),COUNT(*) FROM corporate_actions GROUP BY 1").fetchall())
    return {"rows": len(rows), "updated": len(updates), "kind_changed": changed_kind,
            "resolution": counts}


def _eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-12 * max(1.0, abs(a))
