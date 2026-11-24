"""Call event bus.

emit() is the one way anything (agent, tools, link page, dev endpoints) reports what
happened on a call:

  1. Allocate the next per-call seq atomically
     (UPDATE call_sessions SET last_event_seq = last_event_seq + 1 ... RETURNING).
  2. INSERT the row into call_events in the same transaction and commit.
  3. Only after the commit, publish to in-process subscribers keyed by call_id.

Because rows are durable before anyone hears about them, a websocket can subscribe
first, replay history from the DB, then drain live events, and drop any seq it
already sent. That way no event is lost or duplicated during the handoff.

Pub/sub lives in process (single uvicorn worker). Going multi-worker would mean
swapping EventBus for Postgres LISTEN/NOTIFY or Redis. emit()'s contract stays the same.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import uuid
import weakref
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, select, update

from app.db import SessionLocal
from app.models import CallEvent, CallSession

log = logging.getLogger(__name__)

Event = dict[str, Any]

SUBSCRIBER_QUEUE_SIZE = 1000


class EventType(enum.StrEnum):
    """Every event type the UI understands (CLAUDE.md > Core architecture).

    Stored as plain text in call_events.type. emit() rejects anything not listed here so a
    typo in a tool can't produce an event the link page silently ignores.
    Payload notes are CLAUDE.md's. Later prompts fill in the rest.
    """

    call_status = "call.status"  # {status: dialing|ringing|connected|ended}
    link_minted = "link.minted"
    link_sent = "link.sent"
    link_opened = "link.opened"
    link_revoked = "link.revoked"
    transcript_turn = "transcript.turn"  # {speaker, text_said, text_heard}
    transcript_correction = "transcript.correction"  # {turn_id, corrected_text}
    note_added = "note.added"
    agent_tool_call = "agent.tool_call"  # {name, args, result summary}
    state_cart = "state.cart"
    state_reservation = "state.reservation"
    confirm_requested = "confirm.requested"
    confirm_received = "confirm.received"
    audio_play = "audio.play"


class CallNotFound(LookupError):
    pass


def serialize(row: Any) -> Event:
    return {
        "id": row.id,
        "call_id": str(row.call_id),
        "seq": row.seq,
        "type": row.type,
        "payload": row.payload,
        "created_at": row.created_at.isoformat(),
    }


@dataclass(eq=False)
class Subscription:
    call_id: str
    queue: asyncio.Queue[Event] = field(default_factory=lambda: asyncio.Queue(SUBSCRIBER_QUEUE_SIZE))
    # Set when the subscriber fell too far behind and was dropped. The consumer should
    # close; the client reconnects with ?since=<last seq> and catches up from the DB.
    overflowed: asyncio.Event = field(default_factory=asyncio.Event)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)

    def subscribe(self, call_id: uuid.UUID | str) -> Subscription:
        sub = Subscription(str(call_id))
        self._subs[sub.call_id].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.call_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.call_id]

    def publish(self, call_id: uuid.UUID | str, event: Event) -> None:
        for sub in list(self._subs.get(str(call_id), ())):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("dropping slow subscriber on call %s", call_id)
                self.unsubscribe(sub)
                sub.overflowed.set()

    def subscriber_count(self, call_id: uuid.UUID | str) -> int:
        return len(self._subs.get(str(call_id), ()))


bus = EventBus()

# Serializes emit() per call within this process. Seq allocation is already atomic in
# the DB; this lock also keeps *publish* order equal to seq order.
_call_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock_for(call_id: str) -> asyncio.Lock:
    lock = _call_locks.get(call_id)
    if lock is None:
        lock = asyncio.Lock()
        _call_locks[call_id] = lock
    return lock


async def emit(
    call_id: uuid.UUID | str, type: EventType | str, payload: dict[str, Any] | None = None
) -> Event:
    """Persist an event with the next per-call seq, then publish it. Returns the stored event.

    Raises ValueError for a type not in EventType, CallNotFound if the call_session doesn't exist.
    """
    try:
        etype = EventType(type)
    except ValueError:
        raise ValueError(f"unknown event type {type!r}; add it to app.events.EventType") from None
    cid = uuid.UUID(str(call_id))
    async with _lock_for(str(cid)):
        async with SessionLocal() as session, session.begin():
            seq = (
                await session.execute(
                    update(CallSession)
                    .where(CallSession.id == cid)
                    .values(last_event_seq=CallSession.last_event_seq + 1)
                    .returning(CallSession.last_event_seq)
                )
            ).scalar_one_or_none()
            if seq is None:
                raise CallNotFound(str(cid))
            row = (
                await session.execute(
                    insert(CallEvent)
                    .values(call_id=cid, seq=seq, type=etype.value, payload=payload or {})
                    .returning(
                        CallEvent.id,
                        CallEvent.call_id,
                        CallEvent.seq,
                        CallEvent.type,
                        CallEvent.payload,
                        CallEvent.created_at,
                    )
                )
            ).one()
        event = serialize(row)  # transaction committed at this point
        bus.publish(cid, event)
    return event


async def history(call_id: uuid.UUID | str, since: int = 0) -> list[Event]:
    """Stored events for a call with seq > since, in seq order."""
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(CallEvent)
            .where(CallEvent.call_id == uuid.UUID(str(call_id)), CallEvent.seq > since)
            .order_by(CallEvent.seq)
        )
        return [serialize(r) for r in rows]
