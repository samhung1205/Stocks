"""Explicit, testable security classification for the research universe.

Only ``common_stock`` (ordinary TWSE/TPEx shares, incl. 創新板/KY) is eligible
for stock strategies.  Rules are evaluated in order and each result carries the
``basis`` that decided it, so weaker evidence is visible:

  industry        -- the exchange industry label (authoritative when present)
  name            -- exchange short name (甲特/乙特/... preferred-share marker)
  stocks_type     -- legacy stocks.type == 'etf'
  id_pattern_fallback -- NO metadata; guessed from stock_id shape (least reliable)

``stocks.type`` alone is NOT trusted: TPEx ETFs/ETNs/DRs were stored as 'stock'.
"""
from __future__ import annotations

import re

COMMON_STOCK, ETF, ETN, REIT, DR, PREFERRED, WARRANT, OTHER = (
    "common_stock", "etf", "etn", "reit", "dr", "preferred", "warrant", "other")

_PREFERRED_NAME = re.compile(r"[甲乙丙丁戊己庚辛壬癸]特")
_COMMON_ID = re.compile(r"^[1-9]\d{3}$")
_PREFERRED_ID = re.compile(r"^[1-9]\d{3}[A-Z]$")
_ETF_ID = re.compile(r"^00\d{2,4}[A-Z]?$")
_REIT_ID = re.compile(r"^01\d{3}T$")
_DR_ID = re.compile(r"^91\d{2,4}$")


def classify_security(stock_id: str, name: str | None = None, industry: str | None = None,
                      stocks_type: str | None = None) -> tuple[str, str]:
    """Return ``(class, basis)``."""
    sid = str(stock_id or "")
    industry = industry or ""
    if "ETN" in industry:
        return ETN, "industry"
    if "ETF" in industry:
        return ETF, "industry"
    if "受益證券" in industry:
        return REIT, "industry"
    if "存託憑證" in industry:
        return DR, "industry"
    if "所有證券" in industry:  # TPEx catch-all label used for warrants (…P/…F)
        return WARRANT, "industry"
    if name and _PREFERRED_NAME.search(name) and re.match(r"^\d{4}[A-Z]$", sid):
        return PREFERRED, "name"
    if stocks_type == "etf":
        return ETF, "stocks_type"
    if industry:
        if _COMMON_ID.match(sid):
            return COMMON_STOCK, "industry"
        if _PREFERRED_ID.match(sid):
            return PREFERRED, "id_pattern_fallback"
        return OTHER, "industry"
    # No exchange metadata (legacy/delisted rows): shape of the code only.
    if _COMMON_ID.match(sid):
        return COMMON_STOCK, "id_pattern_fallback"
    if _PREFERRED_ID.match(sid):
        return PREFERRED, "id_pattern_fallback"
    if _ETF_ID.match(sid):
        return ETF, "id_pattern_fallback"
    if _REIT_ID.match(sid):
        return REIT, "id_pattern_fallback"
    if _DR_ID.match(sid):
        return DR, "id_pattern_fallback"
    return OTHER, "id_pattern_fallback"


def is_common_stock(stock_id, name=None, industry=None, stocks_type=None) -> bool:
    return classify_security(stock_id, name, industry, stocks_type)[0] == COMMON_STOCK
