import asyncio
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.db import SessionLocal
from app.events import bus, history
from app.models import CallSession

log = logging.getLogger(__name__)
router = APIRouter()

# Application close codes (4000-4999 are reserved for apps by RFC 6455).
CLOSE_CALL_NOT_FOUND = 4404
CLOSE_SUBSCRIBER_LAGGED = 4409  # client should reconnect with ?since=<last seq>


@router.websocket("/ws/calls/{call_id}")
async def call_events_ws(
    websocket: WebSocket,
    call_id: uuid.UUID,
    since: int = Query(0, ge=0),
) -> None:
    """Replay stored events with seq > since, then stream live ones.

    Each message is one event: {id, call_id, seq, type, payload, created_at}.
    Clients should remember the last seq and reconnect with ?since=<seq>.
    """
    # TODO(prompt-3): AUTH NOT ENFORCED YET. This socket is open to anyone who knows the call id.
    #   Require ?token=<raw link token>, sha256 it, look up link_tokens by token_hash, and reject
    #   (close 4401 before streaming anything) unless token.call_id == call_id, expires_at > now
    #   and revoked_at is null. The director view will need its own auth path.
    await websocket.accept()
    try:
        await _stream(websocket, call_id, since)
    except WebSocketDisconnect:
        pass  # client left mid-stream (tab closed, React StrictMode's throwaway first socket, ...)


async def _stream(websocket: WebSocket, call_id: uuid.UUID, since: int) -> None:
    async with SessionLocal() as session:
        if await session.get(CallSession, call_id) is None:
            await websocket.close(code=CLOSE_CALL_NOT_FOUND, reason="call not found")
            return

    # Subscribe BEFORE reading history so an event committed in between is caught by
    # one or the other. Duplicates are dropped by the seq check below.
    sub = bus.subscribe(call_id)
    try:
        last_seq = since
        for event in await history(call_id, since):
            await websocket.send_json(event)
            last_seq = event["seq"]

        async def pump() -> None:
            nonlocal last_seq
            while True:
                event = await sub.queue.get()
                if event["seq"] <= last_seq:
                    continue
                await websocket.send_json(event)
                last_seq = event["seq"]

        async def watch_client() -> None:
            # Nothing is expected from the client yet. Reading is how a disconnect gets noticed
            # while pump() sits idle on the queue.
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        tasks = {
            asyncio.create_task(pump()),
            asyncio.create_task(watch_client()),
            asyncio.create_task(sub.overflowed.wait()),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.error("event stream for call %s failed", call_id, exc_info=exc)

        if sub.overflowed.is_set():
            await websocket.close(code=CLOSE_SUBSCRIBER_LAGGED, reason="lagged; reconnect with since")
    finally:
        bus.unsubscribe(sub)
