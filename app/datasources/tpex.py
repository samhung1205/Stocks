"""櫃買中心（上櫃）：OpenAPI ＋ 官網 www JSON（歷史回填）。"""
from app.datasources.base import get_json, num, roc_to_iso

OPENAPI = "https://www.tpex.org.tw/openapi/v1"
WWW = "https://www.tpex.org.tw/www/zh-tw"


def _iso_slash(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:]}"


def daily_all_latest() -> list[dict]:
    """最新交易日上櫃全市場收盤（OpenAPI，含權證等需由呼叫端過濾）。"""
    rows = get_json(f"{OPENAPI}/tpex_mainboard_daily_close_quotes", cache_ttl=600)
    out = []
    for r in rows:
        date = roc_to_iso(r.get("Date", ""))
        if not date:
            continue
        chg = num(str(r.get("Change", "")).replace("+", ""))
        out.append({
            "stock_id": r["SecuritiesCompanyCode"], "date": date,
            "open": num(r["Open"]), "high": num(r["High"]),
            "low": num(r["Low"]), "close": num(r["Close"]),
            "change": chg,
            "volume": num(r["TradingShares"]),
            "amount": num(r["TransactionAmount"]),
            "transactions": num(r["TransactionNumber"]),
        })
    return out


def valuations_latest() -> list[dict]:
    rows = get_json(f"{OPENAPI}/tpex_mainboard_peratio_analysis", cache_ttl=600)
    out = []
    for r in rows:
        date = roc_to_iso(r.get("Date", ""))
        if not date:
            continue
        out.append({
            "stock_id": r["SecuritiesCompanyCode"], "date": date,
            "pe": num(r["PriceEarningRatio"]), "pb": num(r["PriceBookRatio"]),
            "dividend_yield": num(r["YieldRatio"]),
        })
    return out


def daily_all_by_date(yyyymmdd: str) -> list[dict]:
    """指定日期上櫃全市場收盤（官網 dailyQuotes，歷史回填）。"""
    data = get_json(f"{WWW}/afterTrading/dailyQuotes",
                    params={"date": _iso_slash(yyyymmdd), "type": "EW", "response": "json"})
    if str(data.get("stat", "")).lower() != "ok":
        return []
    table = next((t for t in data.get("tables", [])
                  if "上櫃股票" in (t.get("title") or "")), None)
    if not table or not table.get("data"):
        return []
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    out = []
    for r in table["data"]:
        # [代號,名稱,收盤,漲跌,開盤,最高,最低,均價,成交股數,成交金額,成交筆數,...]
        out.append({
            "stock_id": str(r[0]).strip(), "date": date,
            "close": num(r[2]), "change": num(str(r[3]).replace("＋", "").replace("－", "-")),
            "open": num(r[4]), "high": num(r[5]), "low": num(r[6]),
            "volume": num(r[8]), "amount": num(r[9]), "transactions": num(r[10]),
        })
    return out


def institutional_by_date(yyyymmdd: str) -> list[dict]:
    """指定日期上櫃三大法人買賣超。

    欄位為 7 組(買/賣/淨)＋合計：外資陸資(不含自營)、外資自營、外資合計、
    投信、自營(自行)、自營(避險)、自營合計、三大法人合計。
    """
    data = get_json(f"{WWW}/insti/dailyTrade",
                    params={"type": "Daily", "sect": "EW",
                            "date": _iso_slash(yyyymmdd), "response": "json"})
    if str(data.get("stat", "")).lower() != "ok":
        return []
    tables = data.get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    out = []
    for r in tables[0]["data"]:
        vals = [num(x) or 0 for x in r[2:]]
        if len(vals) < 22:
            continue
        out.append({
            "stock_id": str(r[0]).strip(), "date": date,
            "foreign_net": vals[8],    # 外資及陸資合計淨額
            "trust_net": vals[11],
            "dealer_net": vals[20],    # 自營商合計淨額
            "total_net": vals[21],
        })
    return out
