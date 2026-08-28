"""REST API 路由。前端（web/）全部透過這層取資料。"""
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.db import engine, query_all, query_one, upsert_many
from app.db.tables import watchlist as watchlist_t
from app.datasources.realtime_base import get_provider
from app.services import (
    alerts as alerts_svc, deep_fetch, journal, market_daily,
    scoring, screener, stock_view,
)
from app.services.finance import service as finance

router = APIRouter(prefix="/api")


# ── 市場/儀表板 ─────────────────────────────────────────
@router.get("/market/dashboard")
def dashboard():
    return market_daily.dashboard_data()


@router.get("/stocks/search")
def search(q: str = Query(..., min_length=1)):
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT stock_id, name, market, industry, type FROM stocks
            WHERE stock_id LIKE :p OR name LIKE :p
            ORDER BY LENGTH(stock_id), stock_id LIMIT 20""", p=f"%{q}%")
    return rows


@router.get("/realtime")
def realtime(ids: str):
    """觀察名單準即時快照（MIS，約 5 秒延遲；未來可換券商源）。"""
    sids = [s.strip() for s in ids.split(",") if s.strip()][:40]
    with engine.begin() as conn:
        rows = query_all(conn, f"""
            SELECT stock_id, market FROM stocks
            WHERE stock_id IN ({','.join(':i' + str(n) for n in range(len(sids)))})""",
            **{f"i{n}": s for n, s in enumerate(sids)})
    provider = get_provider()
    return {"provider": provider.name, "delay_seconds": provider.delay_seconds,
            "quotes": provider.quotes([(r["stock_id"], r["market"]) for r in rows])}


# ── 個股 ────────────────────────────────────────────────
@router.get("/stocks/{stock_id}")
def stock_overview(stock_id: str, background: BackgroundTasks):
    ov = stock_view.overview(stock_id)
    if not ov:
        raise HTTPException(404, f"查無 {stock_id}，請先執行「同步股票清單」")
    # 按需深度抓取：背景執行，前端輪詢資料端點即可漸進看到
    background.add_task(deep_fetch.ensure_deep, stock_id)
    return ov


@router.get("/stocks/{stock_id}/deep")
def stock_deep(stock_id: str, force: bool = False):
    """同步觸發深度抓取（前端「重新整理資料」按鈕）。"""
    return deep_fetch.ensure_deep(stock_id, force=force)


@router.get("/stocks/{stock_id}/kline")
def stock_kline(stock_id: str, days: int = 500):
    return stock_view.kline(stock_id, days)


@router.get("/stocks/{stock_id}/revenue")
def stock_revenue(stock_id: str):
    return stock_view.revenue_series(stock_id)


@router.get("/stocks/{stock_id}/financials")
def stock_financials(stock_id: str):
    return stock_view.financial_series(stock_id)


@router.get("/stocks/{stock_id}/chips")
def stock_chips(stock_id: str):
    return stock_view.chips_series(stock_id)


@router.get("/stocks/{stock_id}/valuation")
def stock_valuation(stock_id: str):
    return stock_view.valuation_band(stock_id)


@router.get("/stocks/{stock_id}/technical")
def stock_technical(stock_id: str):
    return stock_view.technical_summary(stock_id)


@router.get("/stocks/{stock_id}/alerts")
def stock_alerts(stock_id: str):
    return {"alerts": alerts_svc.scan(stock_id),
            "checklist": alerts_svc.manual_checklist(stock_id)}


@router.get("/stocks/{stock_id}/score")
def stock_score(stock_id: str):
    return scoring.get_scorecard(stock_id)


@router.get("/stocks/{stock_id}/summary")
def stock_summary(stock_id: str):
    return scoring.narrative_summary(stock_id)


@router.post("/stocks/{stock_id}/score")
def save_score(stock_id: str, payload: dict):
    scoring.save_manual(stock_id, payload)
    return scoring.get_scorecard(stock_id)


# ── 篩選器 ──────────────────────────────────────────────
@router.get("/screener/presets")
def presets():
    return screener.PRESETS


@router.post("/screener/run")
def run_screen(payload: dict):
    return screener.run(payload.get("params", {}), limit=payload.get("limit", 100))


# ── 觀察名單 ────────────────────────────────────────────
@router.get("/watchlist")
def get_watchlist():
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT w.*, s.name, s.market, s.industry,
                   q.close, q.change, q.date qdate,
                   v.pe, v.dividend_yield
            FROM watchlist w
            JOIN stocks s ON s.stock_id = w.stock_id
            LEFT JOIN daily_quotes q ON q.stock_id = w.stock_id
                AND q.date = (SELECT MAX(date) FROM daily_quotes WHERE stock_id = w.stock_id)
            LEFT JOIN valuations v ON v.stock_id = w.stock_id
                AND v.date = (SELECT MAX(date) FROM valuations WHERE stock_id = w.stock_id)
            ORDER BY w.tier, w.added_at""")
    for r in rows:
        r["alerts"] = alerts_svc.scan(r["stock_id"])
    return rows


@router.post("/watchlist/{stock_id}")
def add_watch(stock_id: str, payload: dict, background: BackgroundTasks):
    with engine.begin() as conn:
        if not query_one(conn, "SELECT 1 x FROM stocks WHERE stock_id=:s", s=stock_id):
            raise HTTPException(404, f"查無 {stock_id}")
        upsert_many(conn, watchlist_t, [{
            "stock_id": stock_id,
            "tier": payload.get("tier", "research"),
            "note": payload.get("note", ""),
            "added_at": dt.datetime.now().isoformat(timespec="seconds"),
        }])
    background.add_task(deep_fetch.ensure_deep, stock_id)
    return {"ok": True}


@router.delete("/watchlist/{stock_id}")
def remove_watch(stock_id: str):
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM watchlist WHERE stock_id=?", (stock_id,))
    return {"ok": True}


# ── 交易日誌 ────────────────────────────────────────────
@router.get("/journal/draft-plan/{stock_id}")
def draft_plan(stock_id: str):
    return journal.draft_plan(stock_id)


@router.post("/journal/position-size")
def position_size(payload: dict):
    return journal.suggest_position(
        float(payload["entry"]), float(payload["stop"]), float(payload["max_loss"]))


@router.get("/journal/plans")
def plans(status: str | None = None):
    return journal.list_plans(status)


@router.post("/journal/plans")
def create_plan(payload: dict):
    if not payload.get("stock_id"):
        raise HTTPException(400, "缺少 stock_id")
    pid = journal.create_plan(payload)
    plan = journal.get_plan(pid)
    return {"id": pid, "missing": journal.plan_completeness(plan)}


@router.post("/journal/plans/{plan_id}/status")
def plan_status(plan_id: int, payload: dict):
    journal.update_plan_status(plan_id, payload.get("status", "open"))
    return {"ok": True}


@router.get("/journal/trades")
def trades_list():
    return journal.list_trades()


@router.post("/journal/trades")
def add_trade(payload: dict):
    for k in ("stock_id", "side", "shares", "price"):
        if not payload.get(k):
            raise HTTPException(400, f"缺少 {k}")
    return {"id": journal.add_trade(payload)}


@router.get("/journal/positions")
def positions():
    return journal.positions()


@router.get("/journal/performance")
def performance():
    return journal.performance()


# ── 財務規劃 / ETF 長期投資 ─────────────────────────────
@router.get("/finance/universe")
def finance_universe(extra: str = ""):
    """ETF 名單、metadata、歷史統計，以及問卷/預設值（前端渲染的唯一來源）。"""
    ids = [s.strip() for s in extra.split(",") if s.strip()]
    return finance.universe_info(ids)


@router.post("/finance/simulate")
def finance_simulate(payload: dict):
    """無狀態試算：吃完整 profile payload，回結果頁需要的全部區塊。"""
    return finance.simulate(payload)


@router.get("/finance/profiles")
def finance_profiles():
    return finance.list_profiles()


@router.post("/finance/profiles")
def finance_save_profile(payload: dict):
    return finance.save_profile(payload)


@router.get("/finance/profiles/new")
def finance_new_profile(name: str = "基準情境"):
    """尚未存檔的空白情境骨架（含預設標的名單）。"""
    return finance.default_profile(name)


@router.get("/finance/profiles/{profile_id}")
def finance_get_profile(profile_id: int):
    p = finance.get_profile(profile_id)
    if not p:
        raise HTTPException(404, f"查無情境 {profile_id}")
    return p


@router.delete("/finance/profiles/{profile_id}")
def finance_delete_profile(profile_id: int):
    return finance.delete_profile(profile_id)


@router.post("/finance/refresh-etf")
def finance_refresh_etf(payload: dict, background: BackgroundTasks):
    """背景補齊名單 ETF 的 5 年日線與 10 年配息（供歷史含息年化報酬）。"""
    ids = payload.get("stock_ids") or None
    background.add_task(finance.refresh_etf_history, ids)
    return {"started": True,
            "note": "背景抓取中（禮貌限速，約 1-2 分鐘），完成後重新整理即可看到歷史報酬"}


# ── 管理/資料維護 ───────────────────────────────────────
@router.post("/admin/sync-stocks")
def admin_sync_stocks():
    return {"synced": market_daily.sync_stock_list()}


@router.post("/admin/update-daily")
def admin_update_daily():
    return market_daily.update_daily()


@router.post("/admin/backfill")
def admin_backfill(payload: dict, background: BackgroundTasks):
    days = int(payload.get("days", 180))
    background.add_task(market_daily.backfill_history, days)
    return {"started": True, "days": days,
            "note": "背景執行中（禮貌限速，180 天約需 8-10 分鐘），可重新整理儀表板查看進度"}


@router.post("/admin/fetch-revenue")
def admin_fetch_revenue(payload: dict, background: BackgroundTasks):
    months = int(payload.get("months", 13))
    background.add_task(market_daily.fetch_month_revenue_bulk, months)
    return {"started": True, "months": months}


@router.get("/admin/status")
def admin_status():
    with engine.begin() as conn:
        return {
            "stocks": query_one(conn, "SELECT COUNT(*) n FROM stocks")["n"],
            "quote_days": query_one(conn, "SELECT COUNT(DISTINCT date) n FROM daily_quotes")["n"],
            "latest_date": query_one(conn, "SELECT MAX(date) d FROM daily_quotes")["d"],
            "revenue_months": query_one(conn, "SELECT COUNT(DISTINCT ym) n FROM month_revenue")["n"],
            "deep_stocks": query_one(conn, "SELECT COUNT(DISTINCT stock_id) n FROM deep_fetch_log")["n"],
            "watchlist": query_one(conn, "SELECT COUNT(*) n FROM watchlist")["n"],
        }
