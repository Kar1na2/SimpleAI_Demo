"""Link tokens: the temporary, secured URL the agent texts to the customer.

- The raw token is 32 random bytes, urlsafe. It exists only in this process's memory and in
  the texted URL. The DB stores sha256(raw) plus call_id, expires_at, opened_at, revoked_at.
- Minted at call.status=connected, revoked at call.status=ended.
- Unknown token: 404. Expired or revoked: 410 (the page shows "This link has expired").
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
import uuid

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.events import EventType, emit
from app.models import LinkToken

TOKEN_BYTES = 32

# call_id -> raw URL, for the director view (GET /api/dev/calls/{id}/link). Memory only.
_live_urls: dict[str, str] = {}


class LinkError(Exception):
    status_code = 400


class LinkNotFound(LinkError):
    status_code = 404


class LinkGone(LinkError):
    status_code = 410

    def __init__(self, message: str, token: "LinkToken"):
        super().__init__(message)
        self.token = token


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def link_url(raw: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/s/{raw}"


def masked_url(raw: str) -> str:
    return link_url(f"{raw[:4]}••••")


def mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "•••"
    cc = f"+{digits[:-10]} " if len(digits) > 10 else ""
    return f"{cc}•••-•••-{digits[-4:]}"


def live_url(call_id: uuid.UUID | str) -> str | None:
    return _live_urls.get(str(call_id))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def mint(call_id: uuid.UUID) -> str:
    """Create the call's link token, emit link.minted (id + expiry, never the token). Returns the raw token."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = _now() + dt.timedelta(minutes=get_settings().link_ttl_minutes)
    async with SessionLocal() as session, session.begin():
        token = LinkToken(token_hash=hash_token(raw), call_id=call_id, expires_at=expires_at)
        session.add(token)
        await session.flush()
        token_id = token.id
    _live_urls[str(call_id)] = link_url(raw)
    await emit(call_id, EventType.link_minted, {"token_id": token_id, "expires_at": expires_at.isoformat()})
    return raw


async def resolve(raw: str) -> LinkToken:
    """The token row for a raw token. Raises LinkNotFound (404) or LinkGone (410)."""
    async with SessionLocal() as session:
        token = await session.scalar(select(LinkToken).where(LinkToken.token_hash == hash_token(raw)))
    if token is None:
        raise LinkNotFound("unknown link")
    if token.revoked_at is not None or token.expires_at <= _now():
        raise LinkGone("This link has expired", token)
    return token


async def mark_opened(token: LinkToken) -> bool:
    """Set opened_at and emit link.opened, only the first time. Returns True if this was the first open."""
    async with SessionLocal() as session, session.begin():
        opened = (
            await session.execute(
                update(LinkToken)
                .where(LinkToken.id == token.id, LinkToken.opened_at.is_(None))
                .values(opened_at=_now())
                .returning(LinkToken.opened_at)
            )
        ).scalar_one_or_none()
    if opened is None:
        return False
    await emit(token.call_id, EventType.link_opened, {"token_id": token.id, "opened_at": opened.isoformat()})
    return True


async def revoke(call_id: uuid.UUID) -> None:
    """Revoke the call's live token(s) and emit link.revoked. Safe to call twice."""
    async with SessionLocal() as session, session.begin():
        ids = (
            await session.scalars(
                update(LinkToken)
                .where(LinkToken.call_id == call_id, LinkToken.revoked_at.is_(None))
                .values(revoked_at=_now())
                .returning(LinkToken.id)
            )
        ).all()
    _live_urls.pop(str(call_id), None)
    for token_id in ids:
        await emit(call_id, EventType.link_revoked, {"token_id": token_id})
