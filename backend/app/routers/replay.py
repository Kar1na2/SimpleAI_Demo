"""Replay takes (dev only, used by the director view).

GET  /api/replay/takes        passing TTS runs (backend/takes + backend/runs), starred first
POST /api/replay              {run, max_gap_ms=700, auto_confirm=false} -> {call_id}: a NEW call
POST /api/replay/star         {run}: copy a run into backend/takes/ (committed to git)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.call.orchestrator import CallBusy
from app.call.replay import DEFAULT_MAX_GAP_MS, ReplayError, list_takes, star, start_replay
from app.call.tts import TTSError

router = APIRouter(prefix="/api/replay", tags=["replay"])


class ReplayIn(BaseModel):
    run: str
    max_gap_ms: int = Field(DEFAULT_MAX_GAP_MS, ge=0, le=10_000)
    auto_confirm: bool = False  # headless checks; recording uses a real click in the link page


class StarIn(BaseModel):
    run: str


@router.get("/takes")
async def takes(max_gap_ms: int = Query(DEFAULT_MAX_GAP_MS, ge=0, le=10_000)) -> list[dict[str, Any]]:
    return list_takes(max_gap_ms)


@router.post("", status_code=201)
async def replay(body: ReplayIn) -> dict[str, uuid.UUID]:
    try:
        run = await start_replay(body.run, body.max_gap_ms, body.auto_confirm)
    except ReplayError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except CallBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except TTSError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    return {"call_id": run.call_id}


@router.post("/star")
async def star_take(body: StarIn) -> dict[str, str]:
    try:
        path = star(body.run)
    except ReplayError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"run": path.name, "path": f"backend/takes/{path.name}"}
