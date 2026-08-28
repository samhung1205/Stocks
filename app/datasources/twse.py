"""證交所（上市）：OpenAPI（免授權）＋ 官網 rwd JSON（歷史回填、禮貌抓取）。"""
import datetime as dt

from app.datasources.base import get_json, num, roc_to_iso

OPENAPI = "https://openapi.twse.com.tw/v1"
RWD = "https://www.twse.com.tw/rwd/zh"


def resolve_latest_trading_date(max_lookback: int = 10) -> str | None:
    """從今天往回找最近一個有完整每日收盤資料的交易日（ISO格式）。

    OpenAPI 的 STOCK_DAY_ALL/BWIBBU_ALL「latest」語意實測固定落後一個交易日
    （官方文件本就記載此限制），但官網 rwd 逐日查詢（MI_INDEX）當天收盤後
    即有資料。每日更新應以此為準，而非 daily_all_latest()，否則會卡在前一日。
    """
    today = dt.date.today()
    for i in range(max_lookback):
        d = today - dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            rows = daily_all_by_date(d.strftime("%Y%m%d"))
        except Exception:
            continue
        if len(rows) > 1000:   # 全市場正常 1000+ 檔，避免採用殘缺回應
            return d.isoformat()
    return None


def daily_all_latest() -> list[dict]:
    """最新交易日全市場收盤（OpenAPI，一次一請求）。

    注意：此端點固定落後一個交易日，僅供備援；每日更新請用
    resolve_latest_trading_date() + daily_all_by_date()。
    """
    rows = get_json(f"{OPENAPI}/exchangeReport/STOCK_DAY_ALL", cache_ttl=600)
    out = []
    for r in rows:
        date = roc_to_iso(r.get("Date", ""))
        if not date:
            continue
        out.append({
            "stock_id": r["Code"], "date": date,
            "open": num(r["OpeningPrice"]), "high": num(r["HighestPrice"]),
            "low": num(r["LowestPrice"]), "close": num(r["ClosingPrice"]),
            "change": num(r["Change"]),
            "volume": num(r["TradeVolume"]), "amount": num(r["TradeValue"]),
            "transactions": num(r["Transaction"]),
        })
    return out


def valuations_latest() -> list[dict]:
    """全市場本益比/殖利率/淨值比（OpenAPI）。"""
    rows = get_json(f"{OPENAPI}/exchangeReport/BWIBBU_ALL", cache_ttl=600)
    out = []
    for r in rows:
        date = roc_to_iso(r.get("Date", ""))
        if not date:
            continue
        out.append({
            "stock_id": r["Code"], "date": date,
            "pe": num(r["PEratio"]), "pb": num(r["PBratio"]),
            "dividend_yield": num(r["DividendYield"]),
        })
    return out


def daily_all_by_date(yyyymmdd: str) -> list[dict]:
    """指定日期全市場收盤（官網 MI_INDEX，歷史回填用）。非交易日回傳 []。"""
    data = get_json(f"{RWD}/afterTrading/MI_INDEX",
                    params={"date": yyyymmdd, "type": "ALLBUT0999", "response": "json"})
    if data.get("stat") != "OK":
        return []
    table = next((t for t in data.get("tables", [])
                  if "每日收盤行情" in (t.get("title") or "")), None)
    if not table:
        return []
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    out = []
    for row in table["data"]:
        # [代號,名稱,成交股數,成交筆數,成交金額,開,高,低,收,漲跌(+/-),漲跌價差,...]
        sign = -1 if "-" in str(row[9]) else 1
        chg = num(row[10])
        out.append({
            "stock_id": row[0].strip(), "date": date,
            "open": num(row[5]), "high": num(row[6]),
            "low": num(row[7]), "close": num(row[8]),
            "change": chg * sign if chg is not None else None,
            "volume": num(row[2]), "amount": num(row[4]),
            "transactions": num(row[3]),
        })
    return out


def institutional_by_date(yyyymmdd: str) -> list[dict]:
    """指定日期全市場三大法人買賣超（T86）。"""
    data = get_json(f"{RWD}/fund/T86",
                    params={"date": yyyymmdd, "selectType": "ALLBUT0999", "response": "json"})
    if data.get("stat") != "OK":
        return []
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    out = []
    for r in data.get("data", []):
        # 欄位: 0代號 4外陸資淨(不含自營) 7外資自營淨 10投信淨 11自營商淨(合計) 18三大法人合計
        foreign = (num(r[4]) or 0) + (num(r[7]) or 0)
        out.append({
            "stock_id": r[0].strip(), "date": date,
            "foreign_net": foreign,
            "trust_net": num(r[10]) or 0,
            "dealer_net": num(r[11]) or 0,
            "total_net": num(r[18]) or 0,
        })
    return out


def taiex_by_date(yyyymmdd: str) -> dict | None:
    """指定日期大盤指數與成交金額。"""
    data = get_json(f"{RWD}/afterTrading/MI_INDEX",
                    params={"date": yyyymmdd, "type": "IND", "response": "json"})
    if data.get("stat") != "OK":
        return None
    out = {"date": f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"}
    for t in data.get("tables", []):
        title = t.get("title") or ""
        if "價格指數(臺灣證券交易所)" in title:
            for row in t["data"]:
                if str(row[0]).strip() == "發行量加權股價指數":
                    sign = -1 if "-" in str(row[2]) else 1
                    out["taiex_close"] = num(row[1])
                    out["taiex_change"] = (num(row[3]) or 0) * sign
                    out["taiex_change_pct"] = (num(row[4]) or 0) * sign
    if "taiex_close" not in out:
        return None
    # 成交金額：FMTQIK 一次回傳整月（以月初為參數以命中快取）
    try:
        fm = get_json(f"{RWD}/afterTrading/FMTQIK",
                      params={"date": f"{yyyymmdd[:6]}01", "response": "json"},
                      cache_ttl=3600)
        if fm.get("stat") == "OK":
            for row in fm.get("data", []):
                if roc_to_iso(row[0]) == out["date"]:
                    out["total_amount"] = num(row[2])
    except Exception:
        pass
    return out


def institutional_market_by_date(yyyymmdd: str) -> dict | None:
    """大盤層級三大法人買賣金額（BFI82U），單位：元。"""
    data = get_json(f"{RWD}/fund/BFI82U",
                    params={"dayDate": yyyymmdd, "type": "day", "response": "json"})
    if data.get("stat") != "OK":
        return None
    out = {}
    for r in data.get("data", []):
        name, net = str(r[0]).replace(" ", ""), num(r[3])
        if name.startswith("外資"):   # 外資及陸資(不含外資自營商)＋外資自營商
            out["foreign_net_amt"] = (out.get("foreign_net_amt") or 0) + (net or 0)
        elif name.startswith("投信"):
            out["trust_net_amt"] = net
        elif name.startswith("自營商"):
            out["dealer_net_amt"] = (out.get("dealer_net_amt") or 0) + (net or 0)
    return out or None


def stock_month_kline(stock_id: str, yyyymmdd: str) -> list[dict]:
    """個股單月日K（官網 STOCK_DAY，備援用；主力走 FinMind）。"""
    data = get_json(f"{RWD}/afterTrading/STOCK_DAY",
                    params={"date": yyyymmdd, "stockNo": stock_id, "response": "json"})
    if data.get("stat") != "OK":
        return []
    out = []
    for r in data.get("data", []):
        date = roc_to_iso(r[0])
        if not date:
            continue
        out.append({
            "stock_id": stock_id, "date": date,
            "volume": num(r[1]), "amount": num(r[2]),
            "open": num(r[3]), "high": num(r[4]),
            "low": num(r[5]), "close": num(r[6]),
            "change": num(str(r[7]).replace("X", "")),
            "transactions": num(r[8]),
        })
    return out
