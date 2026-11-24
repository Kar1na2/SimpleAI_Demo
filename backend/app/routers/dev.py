"""Dev-only endpoints. Mounted only when ENABLE_DEV_ROUTES=true.

POST /api/dev/calls              create a bare call_session (no agent)
POST /api/dev/calls/{id}/emit    emit any CLAUDE.md event type with any payload
GET  /api/dev/calls/{id}/link    the live link URL (raw token, memory only) for the director view
WS   /ws/calls/{id}?since=N      a call's events by call id, no token (unauthenticated: dev only)
"""

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app.db import SessionLocal
from app.events import CallNotFound, EventType, emit
from app.links import live_url
from app.call.orchestrator import ACTIVE
from app.call.scenario import ScenarioError, load_scenario
from app.models import CallDirection, CallSession, CallStatus, Customer
from app.streaming import CLOSE_NOT_FOUND, stream_events

router = APIRouter(tags=["dev"])


class CreateCallIn(BaseModel):
    scenario: str = "dev"
    direction: CallDirection = CallDirection.inbound
    customer_phone: str | None = None
    customer_id: int | None = None


class CreateCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scenario: str
    direction: CallDirection
    status: CallStatus
    customer_phone: str | None


class EmitIn(BaseModel):
    # Any CLAUDE.md event type with any payload. An unknown type gets a 422 listing the valid ones.
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/dev/calls", response_model=CreateCallOut, status_code=201)
async def create_call(body: CreateCallIn | None = Body(default=None)) -> CallSession:
    body = body or CreateCallIn()
    async with SessionLocal() as session, session.begin():
        call = CallSession(
            scenario=body.scenario,
            direction=body.direction,
            customer_phone=body.customer_phone,
            customer_id=body.customer_id,
            status=CallStatus.created,
        )
        session.add(call)
    return call


@router.post("/api/dev/calls/{call_id}/emit", status_code=201)
async def emit_event(call_id: uuid.UUID, body: EmitIn) -> dict[str, Any]:
    try:
        return await emit(call_id, body.type, body.payload)
    except CallNotFound:
        raise HTTPException(status_code=404, detail="call not found") from None


@router.get("/api/dev/calls/{call_id}")
async def call_info(call_id: uuid.UUID) -> dict[str, Any]:
    """What the director's call card shows. Live or replay comes from the in-process registry."""
    async with SessionLocal() as session:
        call = await session.get(CallSession, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        customer = await session.get(Customer, call.customer_id) if call.customer_id else None
    try:
        scenario = load_scenario(call.scenario)
        brand, agent_name = scenario.brand, scenario.agent_name
    except ScenarioError:
        brand = agent_name = None
    run = ACTIVE.get(str(call_id))
    digits = "".join(ch for ch in (call.customer_phone or "") if ch.isdigit())
    return {
        "call_id": str(call.id),
        "scenario": call.scenario,
        "direction": call.direction.value,
        "status": call.status.value,
        "brand": brand,
        "agent_name": agent_name,
        "customer_name": customer.name if customer else None,
        "customer_phone_last4": digits[-4:] or None,
        "kind": getattr(run, "kind", "live") if run else None,
        "replay_of": getattr(run, "run_name", None),
        "link_url": live_url(call_id),
    }


@router.get("/api/dev/calls/{call_id}/link")
async def get_link(call_id: uuid.UUID) -> dict[str, str]:
    url = live_url(call_id)
    if url is None:
        raise HTTPException(status_code=404, detail="no live link for this call in this process")
    return {"url": url}


@router.websocket("/ws/calls/{call_id}")
async def call_events_ws(websocket: WebSocket, call_id: uuid.UUID, since: int = Query(0, ge=0)) -> None:
    """Dev view of any call's events by call id. The public, token-checked socket is /ws/s/{token}."""
    await websocket.accept()
    async with SessionLocal() as session:
        if await session.get(CallSession, call_id) is None:
            await websocket.close(code=CLOSE_NOT_FOUND, reason="call not found")
            return
    await stream_events(websocket, call_id, since)
