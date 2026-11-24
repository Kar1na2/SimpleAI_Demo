"""Replay a recorded live run as a NEW call, with silent gaps shortened.

What's real in a replay: a fresh call_session, a freshly minted link token at Connected, the
link page (GET /api/s/{token} and /ws/s/{token} for the new call), link.opened when a browser
opens it, the Confirm click, and revocation at the end. What's recorded: the live agent's turns,
tool calls and state snapshots, re-emitted in their original order and timing, plus the TTS
clips (same hashes, so the same audio files).

Timing: each gap between consecutive recorded events keeps the time a clip was playing; any
silent part (from the end of the last clip to the next event) is capped at max_gap_ms. Gaps
before Connected (dialing, ringing) are kept as recorded.

Confirm: the replay runs normally through the customer's confirm line, then pauses at the
recorded confirm.received point until a real click on POST /api/s/{token}/confirm (or, with
auto_confirm, posts that itself), then continues.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import update

from app.call import orchestrator
from app.call.checks import RUNS_DIR
from app.call.orchestrator import CallBusy, prepare_call
from app.call.scenario import Scenario
from app.call.tts import TTS, audio_path, clip_hash
from app.db import SessionLocal
from app.events import EventType, emit, last_seqs
from app.links import link_sent_payload, mint, revoke
from app.models import CallSession, CallStatus

log = logging.getLogger(__name__)

TAKES_DIR = Path(__file__).resolve().parents[2] / "takes"
DEFAULT_MAX_GAP_MS = 700
CONFIRM_WAIT_SECONDS = 600
_NAME_RE = re.compile(r"^[a-z]+-\d{8}-\d{6}\.json$")

# Recorded events the replay replaces with real ones for the new call.
_REAL_IN_REPLAY = {EventType.link_minted, EventType.link_opened, EventType.link_revoked, EventType.confirm_received}


class ReplayError(ValueError):
    pass


def run_path(name: str) -> Path:
    """A run file by name: backend/takes/ first, then backend/runs/."""
    if not _NAME_RE.match(name):
        raise ReplayError(f"bad run name {name!r}")
    for d in (TAKES_DIR, RUNS_DIR):
        if (d / name).is_file():
            return d / name
    raise ReplayError(f"no run {name!r} in takes/ or runs/")


def load_run(name: str) -> dict[str, Any]:
    return json.loads(run_path(name).read_text())


def _ms(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp() * 1000


def schedule(events: list[dict[str, Any]], max_gap_ms: int = DEFAULT_MAX_GAP_MS) -> list[float]:
    """Replay offset (ms from start) of each recorded event, silent gaps after Connected capped."""
    if not events:
        return []
    times = [_ms(e["created_at"]) for e in events]
    connected = next(
        (t for e, t in zip(events, times) if e["type"] == EventType.call_status and e["payload"].get("status") == "connected"),
        times[0],
    )
    out = [0.0]
    audio_end = float("-inf")
    for i in range(1, len(events)):
        prev, cur = times[i - 1], times[i]
        if events[i - 1]["type"] == EventType.audio_play:
            audio_end = max(audio_end, prev + events[i - 1]["payload"].get("duration_ms", 0))
        gap = max(0.0, cur - prev)
        if prev < connected:
            out.append(out[-1] + gap)
            continue
        speaking = min(gap, max(0.0, audio_end - prev))
        out.append(out[-1] + speaking + min(gap - speaking, max_gap_ms))
    return out


def take_summary(name: str, data: dict[str, Any], starred: bool, max_gap_ms: int = DEFAULT_MAX_GAP_MS) -> dict[str, Any]:
    offsets = schedule(data["events"], max_gap_ms)
    return {
        "run": name,
        "scenario": data["scenario"],
        "started_at": data["started_at"],
        "recorded_s": data.get("duration_s"),
        "replay_s": round(offsets[-1] / 1000, 1) if offsets else 0,
        "nudges": len(data.get("nudges", [])),
        "starred": starred,
    }


def list_takes(max_gap_ms: int = DEFAULT_MAX_GAP_MS) -> list[dict[str, Any]]:
    """Passing live runs recorded with TTS (replayable), starred takes first, newest first."""
    starred = {p.name for p in TAKES_DIR.glob("*.json")} if TAKES_DIR.is_dir() else set()
    seen: dict[str, dict[str, Any]] = {}
    for d in (TAKES_DIR, RUNS_DIR):
        for p in d.glob("*.json") if d.is_dir() else []:
            if p.name in seen or not _NAME_RE.match(p.name):
                continue
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("passed") and data.get("tts") and data.get("events"):
                seen[p.name] = take_summary(p.name, data, p.name in starred, max_gap_ms)
    newest_first = sorted(seen.values(), key=lambda t: t["started_at"], reverse=True)
    return sorted(newest_first, key=lambda t: not t["starred"])  # stable: starred first, then newest


def star(name: str) -> Path:
    """Copy a passing run into backend/takes/ (committed). Audio stays in the cache (gitignored)."""
    src = run_path(name)
    data = json.loads(src.read_text())
    if not (data.get("passed") and data.get("tts")):
        raise ReplayError("only passing runs recorded with TTS can be starred")
    TAKES_DIR.mkdir(exist_ok=True)
    dst = TAKES_DIR / name
    if src != dst:
        shutil.copyfile(src, dst)
    return dst


async def ensure_audio(manifest: dict[str, dict[str, Any]]) -> None:
    """Regenerate any clip missing from the cache (e.g. a fresh clone with takes/ but no audio)."""
    missing = {h: m for h, m in manifest.items() if audio_path(h) is None}
    if not missing:
        return
    tts = TTS()
    for h, m in missing.items():
        if clip_hash(m["model"], m["voice"], m["instructions"], m["text"]) != h:
            raise ReplayError(f"audio manifest entry {h} doesn't match its parameters")
        await tts.render(m["model"], m["voice"], m["instructions"], m["text"], m["speaker"])
        log.info("regenerated missing clip %s", h)


class ReplayRun:
    kind = "replay"

    def __init__(self, scenario: Scenario, call_id: uuid.UUID, run_name: str, data: dict[str, Any],
                 max_gap_ms: int, auto_confirm: bool):
        self.scenario = scenario
        self.call_id = call_id
        self.run_name = run_name
        self.data = data
        self.max_gap_ms = max_gap_ms
        self.auto_confirm = auto_confirm
        self.mode = "auto" if auto_confirm else "manual"
        self.raw_token: str | None = None
        self.error: str | None = None
        self.waiting_for_confirm = False
        self.task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        try:
            await self._run()
        except asyncio.CancelledError:
            self.error = "replay cancelled"
            raise
        except Exception as e:  # noqa: BLE001 - end the call cleanly below
            self.error = f"{type(e).__name__}: {e}"
            log.warning("replay %s failed: %s", self.call_id, self.error)
        finally:
            try:
                if self.error:
                    await self._set_status(CallStatus.ended, reason="error", error=self.error)
                await revoke(self.call_id)
            finally:
                orchestrator.ACTIVE.pop(str(self.call_id), None)

    async def _run(self) -> None:
        events = self.data["events"]
        offsets = schedule(events, self.max_gap_ms)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        paused = 0.0
        for event, offset in zip(events, offsets, strict=True):
            delay = t0 + paused + offset / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            etype, payload = event["type"], event["payload"]
            if etype == EventType.call_status:
                status = CallStatus(payload["status"])
                extra = {"reason": "replay"} if status == CallStatus.ended else {}
                await self._set_status(status, **extra)
                if status == CallStatus.connected:
                    self.raw_token = await mint(self.call_id)  # a real, fresh token for this call
            elif etype == EventType.link_sent:
                assert self.raw_token
                await emit(self.call_id, etype, link_sent_payload(self.scenario.brand, payload["to"], self.raw_token))
            elif etype == EventType.confirm_received:
                started = loop.time()
                await self._wait_for_confirm()
                paused += loop.time() - started
            elif etype in _REAL_IN_REPLAY:
                continue  # minted at Connected / emitted by the real link page / revoked at the end
            else:
                await emit(self.call_id, etype, payload)

    async def _wait_for_confirm(self) -> None:
        if EventType.confirm_received in await last_seqs(self.call_id):
            return  # the customer already tapped Confirm
        if self.auto_confirm:
            from app.main import app  # late import: app.main imports the routers that import this module

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://link.local") as http:
                r = await http.post(f"/api/s/{self.raw_token}/confirm")
            if r.status_code != 200:
                raise ReplayError(f"auto-confirm failed: {r.status_code} {r.text}")
            return
        self.waiting_for_confirm = True
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + CONFIRM_WAIT_SECONDS
            while EventType.confirm_received not in await last_seqs(self.call_id):
                if loop.time() > deadline:
                    raise ReplayError(f"no Confirm click within {CONFIRM_WAIT_SECONDS}s")
                await asyncio.sleep(0.25)
        finally:
            self.waiting_for_confirm = False

    async def _set_status(self, status: CallStatus, **extra: str) -> None:
        now = dt.datetime.now(dt.UTC)
        values: dict[str, object] = {"status": status}
        if status == CallStatus.connected:
            values["connected_at"] = now
        elif status == CallStatus.ended:
            values["ended_at"] = now
        async with SessionLocal() as session, session.begin():
            await session.execute(update(CallSession).where(CallSession.id == self.call_id).values(**values))
        await emit(self.call_id, EventType.call_status, {"status": status.value, **extra})


async def start_replay(run_name: str, max_gap_ms: int = DEFAULT_MAX_GAP_MS, auto_confirm: bool = False) -> ReplayRun:
    data = load_run(run_name)
    if not (data.get("passed") and data.get("tts")):
        raise ReplayError("only passing runs recorded with TTS can be replayed")
    await ensure_audio(data.get("audio") or {})
    async with orchestrator._start_lock:
        if any(r.scenario.name == data["scenario"] for r in orchestrator.ACTIVE.values()):
            raise CallBusy(f"a {data['scenario']} call is already running")
        scenario, call_id, _ = await prepare_call(data["scenario"])
        run = ReplayRun(scenario, call_id, run_name, data, max_gap_ms, auto_confirm)
        orchestrator.ACTIVE[str(call_id)] = run  # type: ignore[assignment]
        run.task = asyncio.create_task(run.run(), name=f"replay-{call_id}")
    return run
