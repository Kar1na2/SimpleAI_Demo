"""Public link endpoints: what the customer's phone talks to. Every route validates the token.

GET  /api/s/{token}          call snapshot; emits link.opened the first time
WS   /ws/s/{token}?since=N   event history + live events; closes 4410 when revoked/expired
POST /api/s/{token}/confirm  the real Confirm click -> confirm.received (409 if not requested
                             or already received)
Unknown token: 404 (WS close 4404). Expired or revoked: 410 (WS close 4410).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket

from app import links
from app.call import state
from app.call.scenario import ScenarioError, load_scenario
from app.db import SessionLocal
from app.events import EventType, emit, last_seqs
from app.models import CallSession, LinkToken
from app.streaming import CLOSE_LINK_GONE, CLOSE_NOT_FOUND, stream_events

router = APIRouter(tags=["link"])

# Serializes check-then-emit for Confirm clicks per call (single worker), so a double tap
# can't emit confirm.received twice.
_confirm_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _brand(scenario_name: str) -> tuple[str | None, str | None, dict[str, str]]:
    try:
        s = load_scenario(scenario_name)
        return s.brand, s.agent_name, s.theme
    except ScenarioError:  # dev calls have no scenario file
        return None, None, {}


async def _token_or_http(raw: str) -> LinkToken:
    try:
        return await links.resolve(raw)
    except links.LinkGone as e:
        # Brand and colors only, so the expired page can say who it was. No customer data.
        async with SessionLocal() as session:
            call = await session.get(CallSession, e.token.call_id)
        brand, _, theme = _brand(call.scenario) if call else (None, None, {})
        raise HTTPException(status_code=410, detail={"message": str(e), "brand": brand, "theme": theme}) from None
    except links.LinkError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from None


async def call_snapshot(call_id: uuid.UUID, token: LinkToken) -> dict[str, Any]:
    async with SessionLocal() as session:
        call = await session.get(CallSession, call_id)
        assert call is not None
        brand, agent_name, theme = _brand(call.scenario)
        snap: dict[str, Any] = {
            # The event seq this snapshot reflects. The page streams the full history from
            # seq 0 for the transcript, and animates only events after this one.
            "seq": call.last_event_seq,
            "call_id": str(call.id),
            "brand": brand,
            "agent_name": agent_name,
            "theme": theme,
            "scenario": call.scenario,
            "direction": call.direction.value,
            "status": call.status.value,
            "connected_at": call.connected_at.isoformat() if call.connected_at else None,
            "link_created_at": token.created_at.isoformat(),
            "expires_at": token.expires_at.isoformat(),
            "cart": None,
            "reservation": None,
            "slots": None,
        }
        if call.scenario == "order":
            snap["cart"] = await state.cart_snapshot(session, call.id)
        elif call.scenario == "reservation" and call.customer_id is not None:
            r = await state.customer_reservation(session, call.customer_id)
            snap["reservation"] = state.reservation_snapshot(r) if r else None
            snap["slots"] = await state.slots_snapshot(session)
    return snap


@router.get("/api/s/{token}")
async def open_link(token: str) -> dict[str, Any]:
    link = await _token_or_http(token)
    await links.mark_opened(link)
    return await call_snapshot(link.call_id, link)


@router.post("/api/s/{token}/confirm")
async def confirm(token: str) -> dict[str, Any]:
    link = await _token_or_http(token)
    async with _confirm_locks[str(link.call_id)]:
        seqs = await last_seqs(link.call_id)
        if EventType.confirm_requested not in seqs:
            raise HTTPException(status_code=409, detail="Nothing to confirm yet")
        if EventType.confirm_received in seqs:
            raise HTTPException(status_code=409, detail="Already confirmed")
        event = await emit(link.call_id, EventType.confirm_received, {"via": "link"})
    return {"ok": True, "seq": event["seq"]}


@router.websocket("/ws/s/{token}")
async def link_events_ws(websocket: WebSocket, token: str, since: int = Query(0, ge=0)) -> None:
    await websocket.accept()
    try:
        link = await links.resolve(token)
    except links.LinkNotFound:
        await websocket.close(code=CLOSE_NOT_FOUND, reason="unknown link")
        return
    except links.LinkGone:
        await websocket.close(code=CLOSE_LINK_GONE, reason="This link has expired")
        return
    await stream_events(websocket, link.call_id, since, close_on_revoke=True, expires_at=link.expires_at)
