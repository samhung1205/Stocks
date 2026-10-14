"""排程：平日收盤後自動補齊輕量層（Stage L1，見 app/services/data_freshness.py）、
每月 10-12 日抓全市場月營收、每週清理。"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import config

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _revenue_job():
    from app.services import market_daily
    try:
        market_daily.fetch_month_revenue_bulk(months_back=2)
    except Exception as e:
        log.error("revenue bulk failed: %s", e)


def _cleanup_job():
    from app.services import deep_fetch
    from app.identity import system_scope
    try:
        with system_scope():
            deep_fetch.cleanup()
    except Exception as e:
        log.error("cleanup failed: %s", e)


def _daily_refresh_window() -> tuple[int, int]:
    """Poll window: from AUTO_REFRESH_CHECK_TIME's hour through the hour the
    bounded retry window ends. Only bounds POLLING cadence (a resource-use
    optimization); it never decides whether a date counts as fresh/stale --
    that gate is app.services.data_freshness's real trading-day evidence."""
    check_hour = int(config.AUTO_REFRESH_CHECK_TIME.split(":")[0])
    end_hour = min(23, check_hour + max(1, config.AUTO_REFRESH_RETRY_WINDOW_MINUTES // 60 + 1))
    return check_hour, end_hour


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler:
        return _scheduler
    sched = BackgroundScheduler(timezone=config.AUTO_REFRESH_TIMEZONE)
    if config.AUTO_REFRESH_ENABLED:
        from app.services import data_freshness
        start_hour, end_hour = _daily_refresh_window()
        interval = max(1, config.AUTO_REFRESH_POLL_INTERVAL_MINUTES)
        # 平日盤後檢查時間起、bounded 重試時間窗內，低頻輪詢一次
        # market_daily 既有 daily pipeline 是否可補齊（見 data_freshness.attempt_refresh）。
        sched.add_job(data_freshness.scheduler_tick,
                      CronTrigger(day_of_week="mon-fri", hour=f"{start_hour}-{end_hour}",
                                  minute=f"*/{interval}"),
                      id="daily_refresh_tick", replace_existing=True)
    # 月營收法定申報期限為每月 10 日；10/11/12 各跑一次補齊
    sched.add_job(_revenue_job, CronTrigger(day="10-12", hour=20, minute=0),
                  id="monthly_revenue", replace_existing=True)
    sched.add_job(_cleanup_job, CronTrigger(day_of_week="sun", hour=3, minute=0),
                  id="cleanup", replace_existing=True)
    sched.start()
    _scheduler = sched
    log.info("scheduler started")
    return sched
