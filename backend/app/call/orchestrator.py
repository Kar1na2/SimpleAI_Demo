"""Call orchestrator: runs one simulated phone call as a background asyncio task.

Telephony is simulated (CLAUDE.md honesty rule). Everything from `connected` on is real:
the live LLM agent, tool calls against Postgres, the link token, and every event.

Flow
  outbound: dialing -> ringing (~2s) -> connected      inbound: ringing (~2s) -> connected
  connected: mint the link token right away (link.minted), then the agent speaks first.
  Turn loop: once the agent's turn ends, the customer speaks the next beat if its wait_for
  holds. If it doesn't, the agent gets a hidden "[The customer is silent...]" note, at most
  MAX_NUDGES times in a row, then the call aborts naming the stuck beat and unmet condition.
  End: on end_call, when the beats run out after the agent's final turn, or on error:
  call.status ended, token revoked, link.revoked.

Modes: auto POSTs the link's confirm endpoint 1.5s after a confirm beat is spoken, and opens
the link right after link.sent. manual waits up to 180s for a real click (polled from the DB,
so a click handled by another process counts).

Speech: with tts=True every turn is an OpenAI TTS clip (app/call/tts.py). Customer beats are
pre-generated when the call starts; agent lines are generated as they come. transcript.turn and
audio.play are emitted together, then the call waits for the clip's real duration. Without TTS
(fast test runs), pacing falls back to words / 2.7 seconds.

Every live call writes backend/runs/<scenario>-<timestamp>.json when it ends (app/call/checks.py):
all events, the end-state checks, and an audio manifest for replay.

The orchestrator never reads the script to decide what the page shows. It reads call_events
back from the DB (CallView), the same as the link page.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
import uuid
from typing import Literal

import httpx
from sqlalchemy import update

from app.call.agent import Agent
from app.call.reset import reset_scenario_data
from app.call.scenario import Beat, CallView, Scenario, load_scenario
from app.call.tools import ToolContext
from app.call.tts import TTS, Clip
from app.db import SessionLocal
from app.events import EventType, emit, history
from app.links import mint, revoke
from app.models import CallSession, CallStatus, Customer

log = logging.getLogger(__name__)

Mode = Literal["auto", "manual"]

RING_SECONDS = 2.0
WORDS_PER_SECOND = 2.7
CONFIRM_DELAY_SECONDS = 1.5
MANUAL_CONFIRM_TIMEOUT_SECONDS = 180
MAX_NUDGES = 2
SILENCE_SECONDS = 2.0  # how long the customer stays quiet before the agent is nudged
CALL_TIMEOUT_SECONDS = 600

SILENCE_NOTE = "[The customer is silent, waiting for you to continue.]"
CONFIRM_NOTE = "[System: the customer tapped Confirm on the link.]"
CONNECTED_NOTE = {
    "outbound": "[System: the call you placed just connected. {first_name} picked up. Speak first.]",
    "inbound": "[System: an incoming call just connected. Answer it.]",
}


def speech_seconds(text: str) -> float:
    """Estimated time to say `text` at speed 1. Used only when TTS is off; otherwise the mp3 length."""
    return max(0.5, len(text.split()) / WORDS_PER_SECOND)


class CallAborted(RuntimeError):
    pass


class CallBusy(RuntimeError):
    pass


# Calls running in this process, by call id.
ACTIVE: dict[str, CallRun] = {}
_start_lock = asyncio.Lock()


class CallRun:
    def __init__(
        self, scenario: Scenario, call_id: uuid.UUID, customer: Customer, mode: Mode, speed: float, tts: bool = False
    ):
        self.scenario = scenario
        self.call_id = call_id
        self.customer_name = customer.name
        self.mode = mode
        self.speed = speed
        self.view = CallView()
        self.inbox: list[str] = []
        self.turn_count = 0
        self.beat_turns: dict[str, int] = {}
        self.raw_token: str | None = None
        self.agent: Agent | None = None
        self.error: str | None = None
        self.ended_before_beat: str | None = None
        self.nudges: list[str] = []  # beat id per silence nudge (hidden: not an event)
        self.started = time.monotonic()
        self.started_at = dt.datetime.now(dt.UTC)
        self.duration: float | None = None
        self.task: asyncio.Task[None] | None = None
        self.tts = TTS() if tts else None
        self.audio: dict[str, dict] = {}  # clip hash -> manifest (what produced it), for replay
        self._beat_clips: asyncio.Task[dict[str, Clip]] | None = None
        self.checks: list[tuple[str, bool, str]] = []
        self.passed: bool | None = None
        self.run_file: str | None = None
        self._confirms_seen = 0
        self.ctx = ToolContext(
            call_id=call_id,
            scenario=scenario,
            customer_id=scenario.customer_id,
            customer_phone=scenario.customer_phone,
            on_link_sent=self._open_link if mode == "auto" else None,
        )
        self._http: httpx.AsyncClient | None = None

    # --- lifecycle ----------------------------------------------------------------------

    async def run(self) -> None:
        reason = "error"
        if self.tts:
            self._beat_clips = asyncio.create_task(self._pregenerate_beats())
        try:
            reason = await asyncio.wait_for(self._run(), CALL_TIMEOUT_SECONDS)
        except TimeoutError:
            self.error = f"call exceeded {CALL_TIMEOUT_SECONDS}s"
        except asyncio.CancelledError:
            self.error = "call cancelled"
            raise
        except Exception as e:  # noqa: BLE001 - any failure ends the call cleanly below
            self.error = f"{type(e).__name__}: {e}" if not isinstance(e, CallAborted) else str(e)
            log.warning("call %s aborted: %s", self.call_id, self.error)
        finally:
            try:
                await self._finish(reason)
            finally:
                self.duration = time.monotonic() - self.started
                if self._beat_clips and not self._beat_clips.done():
                    self._beat_clips.cancel()
                elif self._beat_clips and not self._beat_clips.cancelled():
                    self._beat_clips.exception()  # mark retrieved; a failure already ended the call
                try:
                    from app.call.checks import record_run  # late import: checks imports this module

                    await record_run(self)
                except Exception:  # noqa: BLE001 - a run file must never break call teardown
                    log.exception("call %s: couldn't write the run file", self.call_id)
                ACTIVE.pop(str(self.call_id), None)
                if self._http:
                    await self._http.aclose()

    async def _run(self) -> str:
        if self.scenario.direction == "outbound":
            await self._set_status(CallStatus.dialing)
            await self._pause(RING_SECONDS / 2)
        await self._set_status(CallStatus.ringing)
        await self._pause(RING_SECONDS)
        await self._set_status(CallStatus.connected)
        self.raw_token = await mint(self.call_id)

        self.agent = Agent(self.scenario, self.ctx, on_say=self._agent_says)
        first_name = self.customer_name.split()[0]
        await self._agent_turn([CONNECTED_NOTE[self.scenario.direction].format(first_name=first_name)])

        beats = list(self.scenario.beats)
        i = nudges = 0
        while True:
            await self._sync()
            if self.ctx.end_requested:
                remaining = [b.id for b in beats[i:] if not b.optional]
                if remaining:
                    self.ended_before_beat = remaining[0]
                    raise CallAborted(f"agent ended the call before beat {remaining[0]}")
                return "agent_hung_up"
            beat = beats[i] if i < len(beats) else None
            if beat is None:
                if self.inbox:
                    await self._agent_turn(self._drain())
                    continue
                return "script_finished"

            unmet = beat.unmet(self.view.beat)
            if not unmet:
                i, nudges = i + 1, 0
                await self._customer_says(beat)
                texts = [beat.heard]
                if beat.action == "confirm":
                    await self._confirm_on_link()
                await self._sync()
                await self._agent_turn(texts + self._drain())
                continue

            if self.inbox:  # e.g. a manual Confirm click arrived before its beat
                await self._agent_turn(self._drain())
                continue
            if nudges >= MAX_NUDGES:
                said = self.view.beat.agent_text or "(nothing)"
                raise CallAborted(
                    f"stuck at beat {beat.id} after {MAX_NUDGES} nudges: waiting for {'; '.join(unmet)}. "
                    f'Agent said since the previous beat: "{said}"'
                )
            nudges += 1
            self.nudges.append(beat.id)
            await self._pause(SILENCE_SECONDS)  # a real pause, so the nudge doesn't sound like an interruption
            log.info("call %s: nudge %d before beat %s (%s)", self.call_id, nudges, beat.id, "; ".join(unmet))
            await self._agent_turn([SILENCE_NOTE])

    async def _finish(self, reason: str) -> None:
        payload: dict[str, str] = {"reason": reason if self.error is None else "error"}
        if self.error:
            payload["error"] = self.error
        await self._set_status(CallStatus.ended, **payload)
        await revoke(self.call_id)

    # --- speech ---------------------------------------------------------------------------

    async def _pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speed)

    async def _pregenerate_beats(self) -> dict[str, Clip]:
        """Every customer beat's clip (the truth, `say`), generated in parallel at call start."""
        assert self.tts is not None
        beats = self.scenario.beats
        clips = await asyncio.gather(*(self.tts.speak(b.say, "customer") for b in beats))
        return {b.id: c for b, c in zip(beats, clips, strict=True)}

    async def _say(self, payload: dict, clip: Clip | None, then: list[tuple[EventType, dict]] | None = None) -> None:
        """Emit the turn and its audio.play together (plus any `then` events), then wait while it's spoken."""
        text = payload["text_said"]
        seconds = (clip.duration_ms / 1000 if clip else speech_seconds(text)) / self.speed
        payload["duration_ms"] = round(seconds * 1000)  # playback length at this speed (typing indicator)
        await emit(self.call_id, EventType.transcript_turn, payload)
        if clip:
            self.audio[clip.hash] = clip.manifest()
            await emit(
                self.call_id,
                EventType.audio_play,
                {
                    "url": clip.url,
                    "hash": clip.hash,
                    "duration_ms": clip.duration_ms,
                    "speaker": payload["speaker"],
                    "name": payload["name"],
                    "turn_id": payload["turn_id"],
                },
            )
        for etype, extra in then or []:
            await emit(self.call_id, etype, extra)
        await asyncio.sleep(seconds)

    def _next_turn_id(self) -> int:
        self.turn_count += 1
        return self.turn_count

    async def _agent_says(self, text: str) -> None:
        clip = await self.tts.speak(text, "agent") if self.tts else None  # before emitting: text and voice start together
        await self._say(
            {
                "turn_id": self._next_turn_id(),
                "speaker": "agent",
                "name": self.scenario.agent_name,
                "text_said": text,
                "text_heard": text,
                "beat_id": None,
            },
            clip,
        )

    async def _customer_says(self, beat: Beat) -> None:
        clip = (await self._beat_clips)[beat.id] if self._beat_clips else None
        turn_id = self._next_turn_id()
        self.beat_turns[beat.id] = turn_id
        payload = {
            "turn_id": turn_id,
            "speaker": "customer",
            "name": self.customer_name,
            "text_said": beat.say,
            "text_heard": beat.heard,
            "beat_id": beat.id,
        }
        if beat.marks:
            payload["marks"] = beat.marks  # e.g. link_accepted: the director's "Customer said yes"
        if beat.action:
            payload["action"] = beat.action  # confirm: a Confirm click is expected right after this line
        then = []
        if beat.corrects:
            # Lands as the customer starts saying it, so the page strikes the wrong words right away.
            corrected = self.scenario.beat(beat.corrects)
            then.append(
                (
                    EventType.transcript_correction,
                    {"turn_id": self.beat_turns[corrected.id], "beat_id": corrected.id, "corrected_text": corrected.say},
                )
            )
        await self._say(payload, clip, then)

    async def _agent_turn(self, texts: list[str]) -> None:
        assert self.agent is not None
        await self.agent.respond(texts)

    # --- events ---------------------------------------------------------------------------

    async def _sync(self) -> None:
        """Read new call_events from the DB into the view. Queue a note per Confirm click."""
        for event in await history(self.call_id, since=self.view.last_seq):
            self.view.apply(event)
            if event["type"] == EventType.confirm_received:
                self.inbox.append(CONFIRM_NOTE)
                self._confirms_seen += 1

    def _drain(self) -> list[str]:
        notes, self.inbox = self.inbox, []
        return notes

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

    # --- the customer's phone (simulated taps on the real link endpoints) ------------------

    def http(self) -> httpx.AsyncClient:
        """In-process client for the public link endpoints (same code path as a browser)."""
        if self._http is None:
            from app.main import app  # late import: app.main imports the routers that import this module

            self._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://link.local")
        return self._http

    async def _open_link(self) -> None:
        r = await self.http().get(f"/api/s/{self.raw_token}")
        if r.status_code != 200:
            raise CallAborted(f"auto-open of the link failed: {r.status_code} {r.text}")

    async def _confirm_on_link(self) -> None:
        if self.mode == "auto":
            await self._pause(CONFIRM_DELAY_SECONDS)
            r = await self.http().post(f"/api/s/{self.raw_token}/confirm")
            if r.status_code != 200:
                raise CallAborted(f"auto-confirm failed: {r.status_code} {r.text}")
            return
        before = self._confirms_seen
        deadline = time.monotonic() + MANUAL_CONFIRM_TIMEOUT_SECONDS
        log.info("call %s: waiting up to %ss for a Confirm click", self.call_id, MANUAL_CONFIRM_TIMEOUT_SECONDS)
        while self._confirms_seen == before and not self.inbox:
            if time.monotonic() > deadline:
                raise CallAborted(f"no Confirm click within {MANUAL_CONFIRM_TIMEOUT_SECONDS}s")
            await asyncio.sleep(0.5)
            await self._sync()


# --- starting calls ---------------------------------------------------------------------------


async def prepare_call(scenario_name: str) -> tuple[Scenario, uuid.UUID, Customer]:
    """Reset the scenario's data and create its call_session row (not started)."""
    scenario = load_scenario(scenario_name)
    await reset_scenario_data(scenario.name, scenario.customer_id)
    async with SessionLocal() as session, session.begin():
        customer = await session.get(Customer, scenario.customer_id)
        if customer is None:
            raise RuntimeError(f"customer {scenario.customer_id} not found; run `make seed`")
        call = CallSession(
            scenario=scenario.name,
            direction=scenario.direction,
            customer_id=customer.id,
            customer_phone=scenario.customer_phone,
            status=CallStatus.created,
        )
        session.add(call)
        await session.flush()
        call_id = call.id
    return scenario, call_id, customer


async def start_call(scenario_name: str, mode: Mode = "auto", speed: float = 1.0, tts: bool = False) -> CallRun:
    """Reset data, create the call, and run it as a background task. One live call per scenario."""
    async with _start_lock:
        if any(r.scenario.name == scenario_name for r in ACTIVE.values()):
            raise CallBusy(f"a {scenario_name} call is already running")
        scenario, call_id, customer = await prepare_call(scenario_name)
        run = CallRun(scenario, call_id, customer, mode, speed, tts=tts)
        ACTIVE[str(call_id)] = run
        run.task = asyncio.create_task(run.run(), name=f"call-{call_id}")
    return run
