"""富果行情 REST API：真即時個股報價（免費方案，需 API 金鑰）。

免費方案僅有 /intraday/quote/{symbol} 逐檔查詢（實測 60 次/分鐘限速，
symbol 不分上市/上櫃自動解析）；全市場快照 snapshot/quotes 需付費方案
（實測回傳 403），故觀察名單逐檔查詢即可，數量不多時完全夠用。
"""
import datetime as dt

import httpx

from app.config import FUGLE_API_KEY
from app.datasources.base import get, num

BASE = "https://api.fugle.tw/marketdata/v1.0/stock"


class FugleAuthError(RuntimeError):
    pass


def _headers() -> dict:
    return {"X-API-KEY": FUGLE_API_KEY}


def quote(symbol: str) -> dict | None:
    """單檔即時報價。查無此代號回傳 None；金鑰無效丟例外。"""
    try:
        resp = get(f"{BASE}/intraday/quote/{symbol}", cache_ttl=1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise FugleAuthError("富果 API 金鑰無效或已過期") from e
        if e.response.status_code == 404:
            return None
        raise
    r = resp.json()
    last = r.get("lastPrice") or r.get("closePrice") or r.get("referencePrice")
    yesterday = r.get("previousClose") or r.get("referencePrice")
    micros = r.get("lastUpdated") or r.get("closeTime")
    time_str = None
    if micros:
        time_str = dt.datetime.fromtimestamp(micros / 1e6).strftime("%H:%M:%S")
    bids, asks = r.get("bids") or [], r.get("asks") or []
    return {
        "stock_id": r.get("symbol"), "name": r.get("name"),
        "last": num(last), "yesterday": num(yesterday),
        "open": num(r.get("openPrice")), "high": num(r.get("highPrice")),
        "low": num(r.get("lowPrice")),
        "volume": num((r.get("total") or {}).get("tradeVolume")),
        "best_bid": num(bids[0]["price"]) if bids else None,
        "best_ask": num(asks[0]["price"]) if asks else None,
        "time": time_str, "date": r.get("date"),
    }
