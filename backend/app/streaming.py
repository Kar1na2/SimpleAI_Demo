"""Websocket event streaming shared by /ws/s/{token} (public) and /ws/calls/{id} (dev only).

Each message is one event: {id, call_id, seq, type, payload, created_at}. The server first
replays stored events with seq > since, then streams live ones. Clients remember the last seq
and reconnect with ?since=<seq>.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from app.events import EventType, bus, history

log = logging.getLogger(__name__)

# Application close codes (4000-4999 are reserved for apps by RFC 6455).
CLOSE_NOT_FOUND = 4404
CLOSE_LAGGED = 4409  # subscriber fell behind: reconnect with ?since=<last seq>
CLOSE_LINK_GONE = 4410  # link expired or revoked: show "This link has expired"


async def stream_events(
    websocket: WebSocket,
    call_id: uuid.UUID,
    since: int = 0,
    *,
    close_on_revoke: bool = False,
    expires_at: dt.datetime | None = None,
) -> None:
    """Replay history after `since`, then stream live events until the client leaves.

    With close_on_revoke, sends link.revoked and then closes with 4410. With expires_at,
    closes with 4410 when the link's TTL runs out.
    """
    # Subscribe BEFORE reading history, so an event committed in between is caught by one
    # or the other. Duplicates are dropped by the seq check.
    sub = bus.subscribe(call_id)
    last_seq = since

    async def send(event: dict) -> bool:
        """Send one event. Returns True if it was link.revoked and the stream must close."""
        nonlocal last_seq
        if event["seq"] <= last_seq:
            return False
        await websocket.send_json(event)
        last_seq = event["seq"]
        return close_on_revoke and event["type"] == EventType.link_revoked

    async def pump() -> str:
        while True:
            if await send(await sub.queue.get()):
                return "revoked"

    async def watch_client() -> str:
        # Nothing is expected from the client. Reading is how a disconnect gets noticed.
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
        return "client_left"

    async def lagged() -> str:
        await sub.overflowed.wait()
        return "lagged"

    async def expiry() -> str:
        assert expires_at is not None
        await asyncio.sleep(max(0.0, (expires_at - dt.datetime.now(dt.UTC)).total_seconds()))
        return "expired"

    try:
        for event in await history(call_id, since):
            if await send(event):
                await websocket.close(code=CLOSE_LINK_GONE, reason="link revoked")
                return

        tasks = {asyncio.create_task(pump()), asyncio.create_task(watch_client()), asyncio.create_task(lagged())}
        if expires_at is not None:
            tasks.add(asyncio.create_task(expiry()))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        reasons = set()
        for t in done:
            exc = t.exception()
            if exc is None:
                reasons.add(t.result())
            elif not isinstance(exc, WebSocketDisconnect):
                log.error("event stream for call %s failed", call_id, exc_info=exc)
        if reasons & {"revoked", "expired"}:
            await websocket.close(code=CLOSE_LINK_GONE, reason="This link has expired")
        elif "lagged" in reasons:
            await websocket.close(code=CLOSE_LAGGED, reason="lagged; reconnect with since")
    except WebSocketDisconnect:
        pass  # client left mid-replay
    finally:
        bus.unsubscribe(sub)
