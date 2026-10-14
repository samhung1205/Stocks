"""台股投資分析平台 — FastAPI 入口。

啟動：uv run uvicorn app.main:app --port 8787
瀏覽器開 http://localhost:8787
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import make_url

from app import config
from app.api.routes import router
from app.db import init_db
from app.access import PrivateAccessMiddleware, AccountAccessMiddleware
from app.identity import system_scope
from starlette.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # An unmounted/empty cloud volume must not silently become a fresh database.
    if config.REQUIRE_EXISTING_DB:
        url = make_url(config.DB_URL)
        if url.get_backend_name() == "sqlite":
            path = Path(url.database or "")
            if not path.is_file():
                raise RuntimeError("Existing SQLite database missing; mount/import the data volume first")
            with path.open("rb") as source:
                if source.read(16) != b"SQLite format 3\x00":
                    raise RuntimeError("Existing SQLite database has an invalid header")
    if config.AUTH_MODE == 'accounts':
        # A real account migration must be backed up and performed explicitly,
        # never as a side effect of starting the web server.
        import sqlite3
        url = make_url(config.DB_URL)
        path = Path(url.database or '')
        if url.get_backend_name() != 'sqlite' or not path.is_file():
            raise RuntimeError('Initialize accounts with scripts/accounts.py init before starting')
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as check:
            try:
                migrated = check.execute('SELECT 1 FROM schema_migrations WHERE version=7').fetchone()
                ready = check.execute('SELECT 1 FROM users WHERE id=1 AND active=1 AND password_hash IS NOT NULL').fetchone()
            except sqlite3.Error:
                migrated = ready = None
        if not migrated or not ready:
            raise RuntimeError('Back up and initialize accounts with scripts/accounts.py init before starting')
    with system_scope():
        init_db()
    from app.services import strategy_lab
    with system_scope():
        strategy_lab.recover_interrupted()
    from app.services import optimization_lab
    with system_scope():
        optimization_lab.recover_interrupted()
    from app.services import walkforward_lab
    with system_scope():
        walkforward_lab.recover_interrupted()
    from app.services import portfolio_lab
    with system_scope():
        portfolio_lab.recover_interrupted()
    # Stage L1: startup catch-up for the daily light-layer dataset (e.g. the
    # laptop was asleep through the scheduled refresh). Runs on a background
    # thread so a slow/unreachable data source never blocks server startup.
    from app.services import data_freshness
    data_freshness.recover_interrupted()
    data_freshness.startup_catchup_async()
    if config.ENABLE_SCHEDULER:
        from app import scheduler
        scheduler.start()
    yield


app = FastAPI(title="台股投資分析平台", lifespan=lifespan)
if config.AUTH_MODE == 'accounts':
    app.add_middleware(AccountAccessMiddleware)
else:
    app.add_middleware(PrivateAccessMiddleware, required=config.REQUIRE_AUTH,
                       username=config.AUTH_USERNAME, password=config.AUTH_PASSWORD)


@app.exception_handler(PermissionError)
async def permission_error(request, exc):
    return JSONResponse({'detail': '無權存取此資料'}, 403)
app.include_router(router)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
