"""排程：平日收盤後更新輕量層、每月 10-12 日抓全市場月營收、每週清理。"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _daily_job():
    from app.services import market_daily, deep_fetch
    from app.db import engine, query_all
    try:
        market_daily.update_daily()
    except Exception as e:
        log.error("daily update failed: %s", e)
    # 觀察名單個股順帶刷新深度資料
    try:
        with engine.begin() as conn:
            watch = query_all(conn, "SELECT stock_id FROM watchlist")
        for r in watch:
            deep_fetch.ensure_deep(r["stock_id"])
    except Exception as e:
        log.error("watchlist deep refresh failed: %s", e)


def _revenue_job():
    from app.services import market_daily
    try:
        market_daily.fetch_month_revenue_bulk(months_back=2)
    except Exception as e:
        log.error("revenue bulk failed: %s", e)


def _cleanup_job():
    from app.services import deep_fetch
    try:
        deep_fetch.cleanup()
    except Exception as e:
        log.error("cleanup failed: %s", e)


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler:
        return _scheduler
    sched = BackgroundScheduler(timezone="Asia/Taipei")
    # 平日 15:00（證交所盤後資料多在 14:30-15:00 就緒；OpenAPI 反映最新交易日）
    sched.add_job(_daily_job, CronTrigger(day_of_week="mon-fri", hour=15, minute=5),
                  id="daily_update", replace_existing=True)
    # 月營收法定申報期限為每月 10 日；10/11/12 各跑一次補齊
    sched.add_job(_revenue_job, CronTrigger(day="10-12", hour=20, minute=0),
                  id="monthly_revenue", replace_existing=True)
    sched.add_job(_cleanup_job, CronTrigger(day_of_week="sun", hour=3, minute=0),
                  id="cleanup", replace_existing=True)
    sched.start()
    _scheduler = sched
    log.info("scheduler started")
    return sched
