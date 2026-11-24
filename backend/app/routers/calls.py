"""Start a scripted call. The director view uses this."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.call.orchestrator import CallBusy, start_call
from app.call.scenario import ScenarioError, list_scenarios
from app.call.tts import TTSError

router = APIRouter(prefix="/api/calls", tags=["calls"])


class StartIn(BaseModel):
    scenario: str
    mode: Literal["auto", "manual"] = "auto"
    speed: float = Field(1.0, gt=0, le=10)
    tts: bool = True  # real voices (director); the CLI defaults to off for fast test runs


class StartOut(BaseModel):
    call_id: uuid.UUID


@router.post("/start", response_model=StartOut, status_code=201)
async def start(body: StartIn) -> StartOut:
    if body.scenario not in list_scenarios():
        raise HTTPException(status_code=404, detail=f"unknown scenario; have {list_scenarios()}")
    try:
        run = await start_call(body.scenario, body.mode, body.speed, tts=body.tts)
    except CallBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except TTSError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except ScenarioError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None
    return StartOut(call_id=run.call_id)
