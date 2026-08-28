"""台股投資分析平台 — FastAPI 入口。

啟動：uv run uvicorn app.main:app --port 8787
瀏覽器開 http://localhost:8787
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.api.routes import router
from app.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if config.ENABLE_SCHEDULER:
        from app import scheduler
        scheduler.start()
    yield


app = FastAPI(title="台股投資分析平台", lifespan=lifespan)
app.include_router(router)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
