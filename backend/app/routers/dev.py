"""Dev-only endpoints for exercising the event bus. Mounted only when ENABLE_DEV_ROUTES=true."""

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db import SessionLocal
from app.events import CallNotFound, EventType, emit
from app.models import CallDirection, CallSession, CallStatus

router = APIRouter(prefix="/api/dev", tags=["dev"])


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


@router.post("/calls", response_model=CreateCallOut, status_code=201)
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


@router.post("/calls/{call_id}/emit", status_code=201)
async def emit_event(call_id: uuid.UUID, body: EmitIn) -> dict[str, Any]:
    try:
        return await emit(call_id, body.type, body.payload)
    except CallNotFound:
        raise HTTPException(status_code=404, detail="call not found") from None
