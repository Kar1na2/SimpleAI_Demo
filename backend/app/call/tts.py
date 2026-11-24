"""OpenAI text-to-speech with a disk cache.

Each clip is keyed by hash(model + voice + instructions + text). It is generated once into
backend/audio_cache/<hash>.mp3 (gitignored), with a <hash>.json sidecar holding what produced
it, and served at /api/audio/<hash>.mp3. Durations are read from the mp3 itself (mutagen).

Two voices (CLAUDE.md): TTS_VOICE_AGENT for the business side, TTS_VOICE_CUSTOMER for the
scripted customer. The customer clip always speaks the beat's `say` (the truth), even when the
transcript shows what the agent misheard.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import openai
from mutagen.mp3 import MP3

from app.config import get_settings

log = logging.getLogger(__name__)

AUDIO_DIR = Path(__file__).resolve().parents[2] / "audio_cache"
Speaker = Literal["agent", "customer"]

# Speaking style, sent as the model's `instructions` (supported by gpt-4o-mini-tts, not tts-1).
INSTRUCTIONS: dict[str, str] = {
    "agent": "Warm, upbeat, professional phone representative. Natural, conversational pace. "
    "Friendly and clear, never rushed.",
    "customer": "Casual, friendly, slightly informal person on a phone call. Relaxed, natural pace.",
}

_HASH_RE = re.compile(r"^[0-9a-f]{32}$")


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class Clip:
    hash: str
    url: str
    duration_ms: int
    speaker: str
    model: str
    voice: str
    instructions: str
    text: str

    def manifest(self) -> dict:
        return asdict(self)


def clip_hash(model: str, voice: str, instructions: str, text: str) -> str:
    return hashlib.sha256(json.dumps([model, voice, instructions, text]).encode()).hexdigest()[:32]


def audio_path(h: str) -> Path | None:
    """Path of a cached clip, or None if the hash is malformed or the file doesn't exist."""
    if not _HASH_RE.match(h):
        return None
    p = AUDIO_DIR / f"{h}.mp3"
    return p if p.is_file() else None


def mp3_duration_ms(path: Path) -> int:
    return round(MP3(path).info.length * 1000)


class TTS:
    def __init__(self) -> None:
        s = get_settings()
        if not s.openai_api_key:
            raise TTSError("OPENAI_API_KEY is not set in backend/.env")
        self.client = openai.AsyncOpenAI(api_key=s.openai_api_key, max_retries=3, timeout=60)
        self.model = s.openai_tts_model
        self.voices = {"agent": s.tts_voice_agent, "customer": s.tts_voice_customer}
        self._locks: dict[str, asyncio.Lock] = {}
        AUDIO_DIR.mkdir(exist_ok=True)

    def _instructions(self, speaker: str) -> str:
        return INSTRUCTIONS[speaker] if not self.model.startswith("tts-1") else ""

    async def speak(self, text: str, speaker: Speaker) -> Clip:
        voice = self.voices[speaker]
        return await self.render(self.model, voice, self._instructions(speaker), text, speaker)

    async def render(self, model: str, voice: str, instructions: str, text: str, speaker: str) -> Clip:
        """The clip for exactly these parameters, generating it only if it isn't cached."""
        h = clip_hash(model, voice, instructions, text)
        path = AUDIO_DIR / f"{h}.mp3"
        async with self._locks.setdefault(h, asyncio.Lock()):
            if not path.is_file():
                kwargs = {"instructions": instructions} if instructions else {}
                try:
                    resp = await self.client.audio.speech.create(
                        model=model, voice=voice, input=text, response_format="mp3", **kwargs
                    )
                except openai.APIError as e:
                    raise TTSError(f"OpenAI TTS failed: {e}") from e
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
        clip = Clip(h, f"/api/audio/{h}.mp3", mp3_duration_ms(path), speaker, model, voice, instructions, text)
        sidecar = path.with_suffix(".json")
        if not sidecar.exists():
            sidecar.write_text(json.dumps(clip.manifest(), indent=2))
        return clip
