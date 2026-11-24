"""Tests run against a separate database (simpleai_test) on the docker-compose Postgres.

No LLM calls anywhere in the test suite. DATABASE_URL is set before any app module is
imported, so the app's engine points at the test database.
"""

import asyncio
import os

import asyncpg
import pytest

ADMIN_DSN = "postgresql://simpleai:simpleai@localhost:5433/simpleai"
TEST_DB = "simpleai_test"
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://simpleai:simpleai@localhost:5433/{TEST_DB}"
os.environ["ENABLE_DEV_ROUTES"] = "true"


async def _ensure_test_db() -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB):
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


asyncio.run(_ensure_test_db())


@pytest.fixture(scope="session", autouse=True)
async def seeded_db():
    from app.db import engine
    from app.seed import seed

    await seed()
    yield
    await engine.dispose()


@pytest.fixture
async def http():
    import httpx

    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def order_call():
    """A fresh order call: reset data + call_session row, no agent."""
    from app.call.orchestrator import prepare_call
    from app.call.tools import ToolContext

    scenario, call_id, _ = await prepare_call("order")
    return ToolContext(call_id, scenario, scenario.customer_id, scenario.customer_phone)


@pytest.fixture
async def reservation_call():
    from app.call.orchestrator import prepare_call
    from app.call.tools import ToolContext

    scenario, call_id, _ = await prepare_call("reservation")
    return ToolContext(call_id, scenario, scenario.customer_id, scenario.customer_phone)
