"""TTS audio clips: GET /api/audio/{hash}.mp3 from backend/audio_cache/ (content-addressed, immutable)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.call.tts import audio_path

router = APIRouter(tags=["media"])


@router.get("/api/audio/{name}")
async def audio(name: str) -> FileResponse:
    path = audio_path(name.removesuffix(".mp3")) if name.endswith(".mp3") else None
    if path is None:
        raise HTTPException(status_code=404, detail="no such clip")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})
