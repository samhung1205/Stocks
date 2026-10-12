"""REST API 路由。前端（web/）全部透過這層取資料。"""
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.db import engine, query_all, query_one, upsert_many
from app.db.tables import watchlist as watchlist_t
from app.datasources.realtime_base import get_provider
from app.services import (
    alerts as alerts_svc, data_freshness, deep_fetch, journal, market_daily,
    scoring, screener, stock_view,
)
from app.services.finance import service as finance
from app.services.finance import portfolio_source as finance_portfolio_source

router = APIRouter(prefix="/api")


@router.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
def health():
    """Non-sensitive deployment readiness check; DB must be reachable."""
    try:
        with engine.connect() as conn:
            query_one(conn, "SELECT 1 AS ready")
    except Exception:
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ok"}


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
    for k in ("entry", "stop", "max_loss"):
        if payload.get(k) is None:
            raise HTTPException(400, f"缺少 {k}")
    return _lab(lambda: journal.suggest_position(
        float(payload["entry"]), float(payload["stop"]), float(payload["max_loss"])))


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
    return _lab(finance.simulate, payload)


@router.get("/finance/portfolio-evidence")
def finance_portfolio_evidence(strategy_portfolio_id: int, portfolio_version_id: int,
                                research_run_id: str | None = None):
    """Stage I1：Strategy Portfolio 作為 Planner investment source 的唯讀 evidence。
    純讀取，不寫入任何 Planner 資料；research_run_id 缺失/不吻合/尚未完成一律回
    `research.available=false` + `reason`，不是 500（見 portfolio_source.evidence）。"""
    return _lab(finance_portfolio_source.evidence, strategy_portfolio_id,
               portfolio_version_id, research_run_id)


@router.get("/finance/profiles")
def finance_profiles():
    return finance.list_profiles()


@router.post("/finance/profiles")
def finance_save_profile(payload: dict):
    return _lab(finance.save_profile, payload)


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
    try:
        return data_freshness.run_manual_update()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/admin/backfill")
def admin_backfill(payload: dict, background: BackgroundTasks):
    days = int(payload.get("days", 180))
    background.add_task(data_freshness.run_manual_backfill, days)
    return {"started": True, "days": days,
            "note": "背景執行中（禮貌限速，180 天約需 8-10 分鐘），可重新整理儀表板查看進度"}


@router.get("/admin/data-freshness")
def admin_data_freshness():
    """Stage L1 統一 freshness/auto-refresh 讀取端點：見 app/services/data_freshness.py。"""
    return data_freshness.status()


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


# Stage A+ data maintenance only; no Strategy routes.
from pydantic import BaseModel, Field
from app.services import history_backfill, data_foundation
from app import config


class BackfillRequest(BaseModel):
    start: str | None = None
    end: str | None = None
    min_avg_amount: float = Field(default=config.BACKFILL_MIN_AVG_AMOUNT, ge=0, allow_inf_nan=False)
    min_observations: int = Field(default=120, ge=1)
    stock_ids: list[str] | None = None


class BackfillResume(BaseModel):
    max_units: int = Field(default=25, ge=1, le=100)


@router.post('/admin/backfill-for-backtest')
def create_history_backfill(payload: BackfillRequest):
    try:
        return history_backfill.create_job(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/admin/backfill-for-backtest')
def history_backfill_status():
    return history_backfill.status()


@router.get('/admin/backfill-for-backtest/{job_id}')
def history_job_status(job_id: str):
    try:
        return history_backfill.status(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/admin/backfill-for-backtest/{job_id}/resume')
def resume_history_backfill(job_id: str, payload: BackfillResume, background: BackgroundTasks):
    try:
        history_backfill.prepare_resume(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(history_backfill.run, job_id, payload.max_units, reset_stop=False)
    return {'id': job_id, 'queued': True, 'max_units': payload.max_units}


@router.post('/admin/backfill-for-backtest/{job_id}/pause')
def pause_history_backfill(job_id: str):
    try:
        return history_backfill.pause(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/admin/data-universe')
def data_universe(as_of: str, min_avg_amount: float = config.BACKFILL_MIN_AVG_AMOUNT,
                  min_observations: int = 120):
    try:
        with engine.connect() as conn:
            rows = data_foundation.liquid_universe(conn, as_of, min_avg_amount, min_observations)
        return {'as_of': as_of, 'min_avg_amount': min_avg_amount, 'rows': rows,
                'note': 'Trailing year before as_of; unknown listing dates remain unknown. No historical index constituents.'}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── Strategy Lab（Stage D1；邏輯全在 services/strategy_lab.py）──────────
from app.services import strategy_lab as lab


def _lab(fn, *args, **kwargs):
    """Map service errors to HTTP: bad input 400, missing 404, wrong state 409."""
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/strategy-lab/templates')
def lab_templates():
    return lab.list_templates()


@router.get('/strategy-lab/templates/{key}')
def lab_template(key: str):
    return _lab(lab.get_template_meta, key)


@router.get('/strategy-lab/strategies')
def lab_strategies(include_archived: bool = False):
    return lab.list_strategies(include_archived)


@router.post('/strategy-lab/strategies')
def lab_create_strategy(payload: dict):
    return _lab(lab.create_strategy, payload)


@router.get('/strategy-lab/strategies/{strategy_id}')
def lab_get_strategy(strategy_id: int):
    return _lab(lab.get_strategy, strategy_id)


@router.patch('/strategy-lab/strategies/{strategy_id}')
def lab_update_strategy(strategy_id: int, payload: dict):
    return _lab(lab.update_strategy, strategy_id, payload)


@router.delete('/strategy-lab/strategies/{strategy_id}')
def lab_archive_strategy(strategy_id: int):
    return _lab(lab.archive_strategy, strategy_id)


@router.get('/strategy-lab/strategies/{strategy_id}/versions')
def lab_versions(strategy_id: int):
    return _lab(lab.list_versions, strategy_id)


@router.get('/strategy-lab/strategies/{strategy_id}/versions/{version_id}')
def lab_version(strategy_id: int, version_id: int):
    return _lab(lab.get_version, strategy_id, version_id)


@router.post('/strategy-lab/strategies/{strategy_id}/runs')
def lab_run_backtest(strategy_id: int, payload: dict, background: BackgroundTasks):
    queued = _lab(lab.create_run, strategy_id, payload)
    if queued['queued']:
        background.add_task(lab.execute_run, queued['run_id'])
    return queued


@router.get('/strategy-lab/strategies/{strategy_id}/runs')
def lab_runs(strategy_id: int, limit: int = 50):
    return _lab(lab.list_runs, strategy_id, limit)


@router.get('/strategy-lab/runs/{run_id}')
def lab_run(run_id: str):
    return _lab(lab.get_run, run_id)


@router.get('/strategy-lab/runs/{run_id}/result')
def lab_run_result(run_id: str):
    return _lab(lab.get_result, run_id)


@router.get('/strategy-lab/runs/{run_id}/equity')
def lab_run_equity(run_id: str):
    return _lab(lab.get_equity, run_id)


@router.get('/strategy-lab/runs/{run_id}/trades')
def lab_run_trades(run_id: str, kind: str = 'closed', limit: int = 500, offset: int = 0):
    return _lab(lab.get_trades, run_id, kind, limit, offset)


# ── Strategy Lab Optimization（Stage E1；邏輯在 services/optimization_lab.py）──
from app.services import optimization_lab as opt


@router.get('/strategy-lab/optimization/search-spaces')
def opt_catalog():
    return opt.catalog()


@router.post('/strategy-lab/optimization/jobs')
def opt_create(payload: dict, background: BackgroundTasks):
    queued = _lab(opt.create_job, payload)
    if queued['queued']:
        background.add_task(opt.execute_job, queued['job_id'])
    return queued


@router.get('/strategy-lab/optimization/jobs')
def opt_jobs(limit: int = 50, template_key: str | None = None):
    return opt.list_jobs(limit, template_key)


@router.get('/strategy-lab/optimization/jobs/{job_id}')
def opt_job(job_id: str):
    return _lab(opt.get_job, job_id)


@router.post('/strategy-lab/optimization/jobs/{job_id}/cancel')
def opt_cancel(job_id: str):
    return _lab(opt.cancel_job, job_id)


@router.post('/strategy-lab/optimization/jobs/{job_id}/resume')
def opt_resume(job_id: str, background: BackgroundTasks, retry_failed: bool = False):
    queued = _lab(opt.resume_job, job_id, retry_failed)
    background.add_task(opt.execute_job, job_id)
    return queued


@router.get('/strategy-lab/optimization/jobs/{job_id}/results')
def opt_results(job_id: str, objective: str | None = None, order: str = 'objective',
                limit: int = 100, offset: int = 0, status: str | None = None):
    return _lab(opt.get_results, job_id, objective, order, limit, offset, status)


@router.get('/strategy-lab/optimization/jobs/{job_id}/surface')
def opt_surface(job_id: str, x: str | None = None, y: str | None = None,
                objective: str | None = None, agg: str = 'median'):
    return _lab(opt.get_surface, job_id, x, y, objective, agg)


@router.get('/strategy-lab/optimization/jobs/{job_id}/robustness')
def opt_robustness(job_id: str, objective: str | None = None, top_fraction: float | None = None):
    return _lab(opt.get_robustness, job_id, objective, top_fraction)


# ── Strategy Lab Stage F0：Walk Forward 研究契約（唯讀；引擎/UI 留待 F1+）──
from app.services import warning_contract
from app.services.walkforward import contracts as wf_contracts
from app.services.walkforward import protocol as wf_protocol


@router.get('/strategy-lab/warning-contract')
def warning_severity_contract():
    return warning_contract.catalog()


@router.get('/strategy-lab/walkforward/protocol')
def walkforward_protocol():
    """Default protocol and the fold plan it yields on the current research window."""
    with engine.connect() as conn:
        sessions = [r['date'] for r in query_all(
            conn, 'SELECT date FROM trading_calendar WHERE date BETWEEN :s AND :e ORDER BY date',
            s=opt.RESEARCH_START, e=opt.RESEARCH_END)]
    return {**wf_protocol.generate_folds(opt.RESEARCH_START, opt.RESEARCH_END, sessions),
            'contracts': wf_contracts.get()}


# ── Strategy Lab Stage F1：Walk Forward / OOS（邏輯在 services/walkforward_lab.py）──
from app.services import walkforward_lab as wflab


@router.get('/strategy-lab/walkforward/contracts')
def walkforward_contracts():
    return {**wf_contracts.get(), 'notice': wflab.NOTICE}


@router.post('/strategy-lab/walkforward/jobs')
def wf_create(payload: dict, background: BackgroundTasks):
    queued = _lab(wflab.create_job, payload)
    if queued['queued']:
        background.add_task(wflab.execute_job, queued['job_id'])
    return queued


@router.get('/strategy-lab/walkforward/jobs')
def wf_jobs(limit: int = 50, template_key: str | None = None):
    return wflab.list_jobs(limit, template_key)


@router.get('/strategy-lab/walkforward/jobs/{job_id}')
def wf_job(job_id: str):
    return _lab(wflab.get_job, job_id)


@router.post('/strategy-lab/walkforward/jobs/{job_id}/cancel')
def wf_cancel(job_id: str):
    return _lab(wflab.cancel_job, job_id)


@router.post('/strategy-lab/walkforward/jobs/{job_id}/resume')
def wf_resume(job_id: str, background: BackgroundTasks):
    queued = _lab(wflab.resume_job, job_id)
    background.add_task(wflab.execute_job, job_id)
    return queued


@router.get('/strategy-lab/walkforward/jobs/{job_id}/result')
def wf_result(job_id: str):
    return _lab(wflab.get_result, job_id)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/folds')
def wf_folds(job_id: str):
    return _lab(wflab.get_folds, job_id)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/folds/{index}')
def wf_fold(job_id: str, index: int):
    return _lab(wflab.get_fold, job_id, index)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/folds/{index}/equity')
def wf_fold_equity(job_id: str, index: int):
    return _lab(wflab.get_equity, job_id, 'fold', index)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/folds/{index}/trades')
def wf_fold_trades(job_id: str, index: int, kind: str = 'fill', limit: int = 500, offset: int = 0):
    return _lab(wflab.get_trades, job_id, 'fold', index, kind, limit, offset)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/oos')
def wf_oos_independent(job_id: str):
    return _lab(wflab.get_independent_oos, job_id)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/stitched')
def wf_stitched_result(job_id: str):
    return _lab(wflab.get_stitched, job_id)


@router.get('/strategy-lab/walkforward/jobs/{job_id}/stitched/equity')
def wf_stitched_equity(job_id: str):
    return _lab(wflab.get_equity, job_id, 'stitched')


@router.get('/strategy-lab/walkforward/jobs/{job_id}/stitched/trades')
def wf_stitched_trades(job_id: str, kind: str = 'fill', limit: int = 500, offset: int = 0):
    return _lab(wflab.get_trades, job_id, 'stitched', -1, kind, limit, offset)


# ── Strategy Lab Stage G：Cross-Strategy Analysis（read-only；邏輯在 services/cross_strategy/）──
from app.services import cross_strategy as xstrat


@router.post('/strategy-lab/cross-strategy/analyze')
def cross_strategy_analyze(payload: dict):
    """``payload``: {"jobs": {strategy_key: walkforward_job_id, ...}}. Read-only — re-reads the
    given completed Walk Forward jobs and never persists or duplicates their data; see
    ``cross_strategy.analyze`` for what "descriptive, no ranking/allocation" means here."""
    jobs = payload.get('jobs') or {}
    if not isinstance(jobs, dict) or not jobs:
        raise HTTPException(400, "payload.jobs must be a non-empty {strategy_key: job_id} map")
    return xstrat.analyze(jobs)


# ── Strategy Portfolio Lab（Stage H1；邏輯在 services/portfolio_lab.py，backend only）──
from app.services import portfolio_lab as plab


@router.get('/strategy-lab/portfolios')
def portfolio_list(include_archived: bool = False):
    return plab.list_portfolios(include_archived)


@router.post('/strategy-lab/portfolios')
def portfolio_create(payload: dict):
    return _lab(plab.create_portfolio, payload)


@router.get('/strategy-lab/portfolios/{portfolio_id}')
def portfolio_get(portfolio_id: int):
    return _lab(plab.get_portfolio, portfolio_id)


@router.patch('/strategy-lab/portfolios/{portfolio_id}')
def portfolio_update(portfolio_id: int, payload: dict):
    return _lab(plab.update_portfolio, portfolio_id, payload)


@router.delete('/strategy-lab/portfolios/{portfolio_id}')
def portfolio_archive(portfolio_id: int):
    return _lab(plab.archive_portfolio, portfolio_id)


@router.post('/strategy-lab/portfolios/{portfolio_id}/clone')
def portfolio_clone(portfolio_id: int, payload: dict):
    return _lab(plab.clone_portfolio, portfolio_id, payload)


@router.get('/strategy-lab/portfolios/{portfolio_id}/versions')
def portfolio_versions(portfolio_id: int):
    return _lab(plab.list_versions, portfolio_id)


@router.get('/strategy-lab/portfolios/{portfolio_id}/versions/{version_id}')
def portfolio_version(portfolio_id: int, version_id: int):
    return _lab(plab.get_version, portfolio_id, version_id)


@router.post('/strategy-lab/portfolios/{portfolio_id}/research-runs')
def portfolio_run_create(portfolio_id: int, payload: dict, background: BackgroundTasks):
    queued = _lab(plab.create_research_run, portfolio_id, payload)
    if queued['queued']:
        background.add_task(plab.execute_research_run, queued['run_id'])
    return queued


@router.get('/strategy-lab/portfolios/{portfolio_id}/research-runs')
def portfolio_runs(portfolio_id: int, limit: int = 50):
    return _lab(plab.list_research_runs, portfolio_id, limit)


@router.get('/strategy-lab/portfolio-research-runs/{run_id}')
def portfolio_run_get(run_id: str):
    return _lab(plab.get_research_run, run_id)


@router.post('/strategy-lab/portfolio-research-runs/{run_id}/cancel')
def portfolio_run_cancel(run_id: str):
    return _lab(plab.cancel_research_run, run_id)


@router.post('/strategy-lab/portfolio-research-runs/{run_id}/resume')
def portfolio_run_resume(run_id: str, background: BackgroundTasks):
    queued = _lab(plab.resume_research_run, run_id)
    background.add_task(plab.execute_research_run, run_id)
    return queued


@router.get('/strategy-lab/portfolio-research-runs/{run_id}/result')
def portfolio_run_result(run_id: str):
    return _lab(plab.get_research_result, run_id)


@router.get('/strategy-lab/portfolio-research-runs/{run_id}/equity')
def portfolio_run_equity(run_id: str):
    return _lab(plab.get_research_equity, run_id)


@router.get('/strategy-lab/portfolio-research-runs/{run_id}/rebalances')
def portfolio_run_rebalances(run_id: str):
    return _lab(plab.get_research_rebalances, run_id)
