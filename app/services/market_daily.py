"""輕量層維護：全市場股票清單、每日收盤/估值/法人、大盤摘要、歷史回填。"""
import datetime as dt
import logging

from app.db import engine, upsert_many, query_all, query_one
from app.db.tables import (
    stocks, daily_quotes, valuations, institutional_daily, market_summary,
    month_revenue,
)
from app.datasources import twse, tpex, finmind, mops

log = logging.getLogger(__name__)


def sync_stock_list() -> int:
    """以 FinMind TaiwanStockInfo 建立全市場股票/ETF 主檔（一次請求）。"""
    rows = finmind.stock_info()
    now = dt.datetime.now().isoformat(timespec="seconds")
    seen: dict[str, dict] = {}
    for r in rows:
        sid = str(r.get("stock_id", "")).strip()
        market = r.get("type", "")
        if market not in ("twse", "tpex") or not (4 <= len(sid) <= 6) or not sid[:4].isdigit():
            continue
        cat = r.get("industry_category") or ""
        row = {
            "stock_id": sid, "name": r.get("stock_name", ""),
            "market": market, "industry": cat,
            "type": "etf" if cat == "ETF" else "stock",
            "updated_at": now,
        }
        # 同代號多筆（多產業分類）時保留第一個非 ETF 分類
        if sid not in seen or seen[sid]["industry"] in ("", "ETF"):
            seen[sid] = row
    with engine.begin() as conn:
        n = upsert_many(conn, stocks, list(seen.values()))
    log.info("stock list synced: %s", n)
    return n


def _known_ids(conn) -> set[str]:
    return {r["stock_id"] for r in query_all(conn, "SELECT stock_id FROM stocks")}


def update_daily() -> dict:
    """收盤後執行：全市場當日收盤、估值、法人、大盤摘要（共 6~8 個請求）。

    收盤價一律以 resolve_latest_trading_date() 找到的實際交易日為準，
    再用逐日查詢（daily_all_by_date）抓該日資料——OpenAPI 的
    daily_all_latest()/valuations_latest() 只當估值備援，因為它們的
    「latest」固定落後一個交易日，若拿來當基準日會讓整批資料卡在昨天。
    """
    result = {}
    with engine.begin() as conn:
        known = _known_ids(conn)
    if not known:
        sync_stock_list()
        with engine.begin() as conn:
            known = _known_ids(conn)

    trade_date = twse.resolve_latest_trading_date()
    result["trade_date"] = trade_date
    if not trade_date:
        result["error"] = "找不到近期任何有效交易日資料"
        log.warning("daily update: %s", result)
        return result
    ymd = trade_date.replace("-", "")

    tw_rows = [r for r in twse.daily_all_by_date(ymd) if r["stock_id"] in known]
    tp_rows = [r for r in tpex.daily_all_by_date(ymd) if r["stock_id"] in known]
    # 估值（本益比/淨值比/殖利率）常比收盤價晚一天發布，抓不到當天就沿用
    # OpenAPI 目前的「latest」（會標記為它實際代表的日期，不強塞今天）
    val_rows = [r for r in twse.valuations_latest() if r["stock_id"] in known]
    val_rows += [r for r in tpex.valuations_latest() if r["stock_id"] in known]

    with engine.begin() as conn:
        result["quotes"] = upsert_many(conn, daily_quotes, tw_rows + tp_rows)
        result["valuations"] = upsert_many(conn, valuations, val_rows)

    inst = [r for r in twse.institutional_by_date(ymd) if r["stock_id"] in known]
    inst += [r for r in tpex.institutional_by_date(ymd) if r["stock_id"] in known]
    summ = twse.taiex_by_date(ymd) or {}
    amt = twse.institutional_market_by_date(ymd) or {}
    with engine.begin() as conn:
        result["institutional"] = upsert_many(conn, institutional_daily, inst)
        if summ:
            summ.update(amt)
            result["summary"] = upsert_many(conn, market_summary, [summ])
    log.info("daily update done: %s", result)
    return result


def backfill_history(days: int = 180) -> dict:
    """回填近 N 個日曆日的全市場日線＋法人（每交易日 4 個請求，禮貌限速）。

    已入庫的日期自動跳過，可分多次執行、可中斷續跑。
    """
    with engine.begin() as conn:
        known = _known_ids(conn)
        have = {r["date"] for r in query_all(
            conn, "SELECT DISTINCT date FROM market_summary")}
    if not known:
        sync_stock_list()
        with engine.begin() as conn:
            known = _known_ids(conn)

    today = dt.date.today()
    done, skipped = 0, 0
    for i in range(days):
        d = today - dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        iso = d.isoformat()
        if iso in have:
            skipped += 1
            continue
        ymd = iso.replace("-", "")
        try:
            quotes = [r for r in twse.daily_all_by_date(ymd) if r["stock_id"] in known]
            if not quotes:          # 非交易日（假日）
                continue
            quotes += [r for r in tpex.daily_all_by_date(ymd) if r["stock_id"] in known]
            inst = [r for r in twse.institutional_by_date(ymd) if r["stock_id"] in known]
            inst += [r for r in tpex.institutional_by_date(ymd) if r["stock_id"] in known]
            summ = twse.taiex_by_date(ymd)
            with engine.begin() as conn:
                upsert_many(conn, daily_quotes, quotes)
                upsert_many(conn, institutional_daily, inst)
                if summ:
                    upsert_many(conn, market_summary, [summ])
            done += 1
        except Exception as e:
            log.warning("backfill %s failed: %s", iso, e)
    return {"days_filled": done, "days_skipped": skipped}


def fetch_month_revenue_bulk(months_back: int = 13) -> dict:
    """從 MOPS 抓近 N 個月「全市場」月營收（每月 2 個請求）。供篩選器用。"""
    with engine.begin() as conn:
        have = {r["ym"] for r in query_all(
            conn, "SELECT ym, COUNT(*) c FROM month_revenue GROUP BY ym HAVING c > 500")}
    today = dt.date.today()
    filled = []
    y, m = today.year, today.month
    for _ in range(months_back):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        ym = f"{y}-{m:02d}"
        if ym in have:
            continue
        rows = mops.monthly_revenue_all(y, m)
        if rows:
            with engine.begin() as conn:
                upsert_many(conn, month_revenue, rows)
            filled.append(ym)
    return {"months_filled": filled}


def dashboard_data() -> dict:
    """儀表板：大盤近況、法人金額、市場寬度。"""
    with engine.begin() as conn:
        summary = query_all(
            conn, "SELECT * FROM market_summary ORDER BY date DESC LIMIT 60")
        latest = summary[0] if summary else None
        breadth = None
        if latest:
            breadth = query_one(conn, """
                SELECT SUM(CASE WHEN change > 0 THEN 1 ELSE 0 END) AS up,
                       SUM(CASE WHEN change < 0 THEN 1 ELSE 0 END) AS down,
                       COUNT(*) AS total
                FROM daily_quotes WHERE date = :d""", d=latest["date"])
        counts = query_one(conn, """
            SELECT (SELECT COUNT(*) FROM stocks) AS stocks,
                   (SELECT COUNT(DISTINCT date) FROM market_summary) AS days,
                   (SELECT COUNT(*) FROM watchlist) AS watchlist""")
    return {"latest": latest, "history": list(reversed(summary)),
            "breadth": breadth, "counts": counts}
