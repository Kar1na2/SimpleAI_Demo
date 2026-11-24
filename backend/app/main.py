import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import engine, init_db
from app.routers import dev, ws

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(title="SimpleAI Demo", lifespan=lifespan)


@app.get("/api/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health check reports any DB failure
        return JSONResponse(
            status_code=503, content={"status": "error", "db": "unreachable", "error": type(exc).__name__}
        )
    return {"status": "ok", "db": "ok"}


app.include_router(ws.router)
if get_settings().enable_dev_routes:
    app.include_router(dev.router)
