"""ETF 歷史統計（spec §13）：從已入庫的日線與配息算「含息年化報酬」。

這是**歷史參考值**，與使用者設定的「假設未來報酬」在介面上必須分開呈現——
spec §13 特別要求不要把歷史績效直接當成未來保證。

含息總報酬的算法：從第一天持有 1 股開始，每次除息時把股利以當日收盤價再投入
（shares *= 1 + dividend / close），期末價值 = shares × 最後收盤價。
CAGR = (期末 / 期初)^(1/年數) − 1。

**分割還原**：FinMind 的 TaiwanStockPrice 回傳的是「未還原價」，遇到股票分割會出現
斷崖式落差（實測 0050 於 2025-06-18 執行 1 股換 4 股，收盤價 188.65 → 47.57）。
不處理的話會被算成 -75% 的崩跌，含息年化直接變負值——這正是 spec 最忌諱的誤導。
台股單日漲跌幅上限 ±10%，所以單日 ratio 落在 [0.55, 1.8] 之外必定是公司行動而非行情，
用這個門檻偵測並把該日以前的價格與股利一併還原。

資料窗長度依 DB 實際有的資料而定，會如實回傳 window_years，讓介面標明
「近 X 年」而不是含糊地說「歷史報酬」。不足 1 年一律回 None。

這個模組會讀 DB（daily_quotes / dividends），是 finance 套件裡少數不純的一層。
"""
import datetime as dt
import logging
import math

from app.db import engine, query_all, upsert_many
from app.db.tables import daily_quotes, dividends as dividends_t
from app.services.finance import universe

log = logging.getLogger(__name__)

MIN_DAYS = 240              # 少於約一年的交易日就不給年化數字
HISTORY_YEARS = 5
DIVIDEND_YEARS = 10
TRADING_DAYS = 252
# 台股單日漲跌幅上限 ±10%，超出這個區間的單日變動視為分割等公司行動
SPLIT_LO, SPLIT_HI = 0.55, 1.8


def stats(stock_id: str) -> dict:
    """單檔的歷史含息年化報酬、價格年化報酬、年化波動、最大回撤。"""
    if stock_id == "CASH":
        return _empty("現金部位不做歷史報酬統計")
    with engine.begin() as conn:
        prices = query_all(conn, """
            SELECT date, close FROM daily_quotes
            WHERE stock_id=:s AND close IS NOT NULL AND close > 0
            ORDER BY date""", s=stock_id)
        divs = query_all(conn, """
            SELECT date, dividend FROM dividends
            WHERE stock_id=:s AND dividend IS NOT NULL AND dividend > 0
            ORDER BY date""", s=stock_id)
    return compute(prices, divs)


def split_factors(closes: list[float]) -> tuple[list[float], list[dict]]:
    """回推每一天的還原係數：分割日之前的價格要乘上該次的價格比。

    由後往前累乘，最新一段永遠是 1.0（以現在的股數為基準）。
    """
    n = len(closes)
    factors = [1.0] * n
    events: list[dict] = []
    f = 1.0
    for i in range(n - 1, 0, -1):
        prev = closes[i - 1]
        r = closes[i] / prev if prev else 1.0
        if r < SPLIT_LO or r > SPLIT_HI:
            f *= r
            events.append({"index": i, "ratio": round(r, 4)})
        factors[i - 1] = f
    return factors, events


def compute(prices: list[dict], divs: list[dict]) -> dict:
    """純計算部分（拆出來方便單測，不碰 DB）。"""
    if len(prices) < MIN_DAYS:
        return _empty(f"資料不足（目前僅 {len(prices)} 個交易日，"
                      f"需要至少 {MIN_DAYS} 天才計算年化數字）",
                      days=len(prices))

    first, last = prices[0], prices[-1]
    d0 = dt.date.fromisoformat(first["date"])
    d1 = dt.date.fromisoformat(last["date"])
    years = (d1 - d0).days / 365.25
    if years < 1:
        return _empty("資料窗不足 1 年", days=len(prices))

    raw = [p["close"] for p in prices]
    factors, raw_events = split_factors(raw)
    closes = [c * f for c, f in zip(raw, factors)]
    split_events = [{"date": prices[e["index"]]["date"], "ratio": e["ratio"]}
                    for e in raw_events]
    # 股利也要套同一天的還原係數，否則分割前的每股配息會被高估
    factor_by_date = {p["date"]: f for p, f in zip(prices, factors)}
    idx_by_date = {p["date"]: i for i, p in enumerate(prices)}
    close_by_date = {p["date"]: c for p, c in zip(prices, closes)}

    shares, div_total = 1.0, 0.0
    for d in divs:
        if not (first["date"] <= d["date"] <= last["date"]):
            continue
        # 除息當天沒有收盤價（如停牌）就往後找最近一個交易日
        if d["date"] in close_by_date:
            px, fac = close_by_date[d["date"]], factor_by_date[d["date"]]
        else:
            nxt = next((i for i, p in enumerate(prices) if p["date"] >= d["date"]), None)
            if nxt is None:
                continue
            px, fac = closes[nxt], factors[nxt]
        if not px:
            continue
        shares *= 1 + (d["dividend"] * fac) / px
        div_total += d["dividend"] * fac

    total_return = (shares * closes[-1]) / closes[0]
    price_return = closes[-1] / closes[0]

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    vol = None
    if len(rets) > 30:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        vol = round(math.sqrt(var * TRADING_DAYS) * 100, 1)

    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)

    return {
        "available": True, "reason": None,
        "days": len(prices),
        "start": first["date"], "end": last["date"],
        "window_years": round(years, 1),
        "cagr_total": round((total_return ** (1 / years) - 1) * 100, 2),
        "cagr_price": round((price_return ** (1 / years) - 1) * 100, 2),
        "total_return_pct": round((total_return - 1) * 100, 1),
        "dividend_count": len([d for d in divs
                               if first["date"] <= d["date"] <= last["date"]]),
        "dividend_total": round(div_total, 2),
        "volatility": vol,
        "max_drawdown": round(mdd * 100, 1),
        "split_events": split_events,
    }


def _empty(reason: str, days: int = 0) -> dict:
    return {"available": False, "reason": reason, "days": days,
            "start": None, "end": None, "window_years": None,
            "cagr_total": None, "cagr_price": None, "total_return_pct": None,
            "dividend_count": 0, "dividend_total": 0,
            "volatility": None, "max_drawdown": None, "split_events": []}


def stats_many(stock_ids: list[str]) -> dict[str, dict]:
    return {sid: stats(sid) for sid in stock_ids}


def refresh(stock_ids: list[str] | None = None) -> dict:
    """補齊名單 ETF 的 5 年日線與 10 年配息。

    刻意不走 deep_fetch.ensure_deep()——那會連帶去抓 ETF 根本沒有的月營收與財報，
    白費約 40 次 FinMind 額度。這裡只抓真正需要的兩個 dataset，
    禮貌限速由 datasources/base.py 既有機制處理。
    """
    from app.datasources import finmind

    ids = [s for s in (stock_ids or list(universe.UNIVERSE)) if s != "CASH"]
    start = (dt.date.today() - dt.timedelta(days=365 * HISTORY_YEARS)).isoformat()
    div_start = (dt.date.today() - dt.timedelta(days=365 * DIVIDEND_YEARS)).isoformat()
    result: dict[str, dict] = {}
    for sid in ids:
        r: dict[str, int | str] = {}
        try:
            rows = finmind.price_history(sid, start)
            with engine.begin() as conn:
                upsert_many(conn, daily_quotes, rows)
            r["price"] = len(rows)
        except Exception as e:
            log.warning("etf refresh price %s: %s", sid, e)
            r["price"] = f"error: {e}"
        try:
            rows = finmind.dividend_results(sid, div_start)
            with engine.begin() as conn:
                upsert_many(conn, dividends_t, rows)
            r["dividend"] = len(rows)
        except Exception as e:
            log.warning("etf refresh dividend %s: %s", sid, e)
            r["dividend"] = f"error: {e}"
        result[sid] = r
    log.info("etf history refreshed: %s", list(result))
    return result
