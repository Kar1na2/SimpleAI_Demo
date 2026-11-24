"""Link tokens: hash-only storage, 404/410, first-open event, confirm 409 cases."""

import hashlib
import json

from sqlalchemy import select, text

from app.db import SessionLocal
from app.events import EventType, emit, history
from app.links import mask_phone, mint, revoke
from app.models import LinkToken


async def test_token_is_stored_only_as_sha256(order_call):
    raw = await mint(order_call.call_id)
    assert len(raw) >= 43  # 32 random bytes, urlsafe base64

    async with SessionLocal() as s:
        row = await s.scalar(select(LinkToken).where(LinkToken.call_id == order_call.call_id))
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        # The raw token appears in no column of link_tokens or call_events.
        dump = await s.scalar(text("SELECT string_agg(t::text, ' ') FROM link_tokens t"))
        events = await s.scalar(text("SELECT string_agg(e::text, ' ') FROM call_events e"))
    assert raw not in dump and raw not in events

    minted = next(e for e in await history(order_call.call_id) if e["type"] == EventType.link_minted)
    assert set(minted["payload"]) == {"token_id", "expires_at"}


async def test_get_link_404_unknown_410_after_revoke(order_call, http):
    assert (await http.get("/api/s/not-a-real-token")).status_code == 404

    raw = await mint(order_call.call_id)
    first = await http.get(f"/api/s/{raw}")
    assert first.status_code == 200
    assert first.json()["brand"] == "Fieldstone Provisions"
    assert first.json()["cart"]["items"] == []
    assert (await http.get(f"/api/s/{raw}")).status_code == 200
    opened = [e for e in await history(order_call.call_id) if e["type"] == EventType.link_opened]
    assert len(opened) == 1, "link.opened is emitted only the first time"

    await revoke(order_call.call_id)
    assert (await http.get(f"/api/s/{raw}")).status_code == 410
    assert (await http.post(f"/api/s/{raw}/confirm")).status_code == 410
    assert [e["type"] for e in await history(order_call.call_id)][-1] == EventType.link_revoked


async def test_confirm_409_until_requested_and_after_received(reservation_call, http):
    raw = await mint(reservation_call.call_id)
    r = await http.post(f"/api/s/{raw}/confirm")
    assert r.status_code == 409 and "Nothing to confirm" in r.text

    await emit(reservation_call.call_id, EventType.confirm_requested, {"summary": "party of 5"})
    assert (await http.post(f"/api/s/{raw}/confirm")).status_code == 200
    r = await http.post(f"/api/s/{raw}/confirm")
    assert r.status_code == 409 and "Already confirmed" in r.text
    received = [e for e in await history(reservation_call.call_id) if e["type"] == EventType.confirm_received]
    assert len(received) == 1


async def test_reservation_snapshot_and_masking(reservation_call, http):
    raw = await mint(reservation_call.call_id)
    body = (await http.get(f"/api/s/{raw}")).json()
    assert body["reservation"]["party_size"] == 4
    assert (body["reservation"]["date"], body["reservation"]["time"]) == ("2026-12-05", "19:00")
    assert {"date": "2026-12-04", "time": "19:00", "status": "full"} in body["slots"]
    assert mask_phone("+15555550187") == "+1 •••-•••-0187"
    assert json.dumps(body).count(raw) == 0


async def test_cart_snapshot_has_shipping_and_totals(order_call, http):
    from app.call.tools import run_tool

    raw = await mint(order_call.call_id)
    await run_tool(order_call, "add_to_cart", {"sku": "FP-RIBEYE-DA16", "qty": 2})
    cart = (await http.get(f"/api/s/{raw}")).json()["cart"]
    assert (cart["subtotal_cents"], cart["shipping_cents"], cart["total_cents"]) == (14400, 1500, 15900)
    assert cart["amount_to_free_shipping_cents"] == 600 and not cart["free_shipping"]
    await run_tool(order_call, "add_to_cart", {"sku": "FP-BUTTER-GH", "qty": 1})
    cart = (await http.get(f"/api/s/{raw}")).json()["cart"]
    assert (cart["subtotal_cents"], cart["shipping_cents"], cart["total_cents"]) == (15600, 0, 15600)
    assert cart["free_shipping"] and cart["amount_to_free_shipping_cents"] == 0


async def test_reservation_snapshot_shows_booked_vs_pending(reservation_call):
    from app.call.tools import run_tool

    await run_tool(reservation_call, "update_reservation", {"party_size": 5, "date": "2026-12-04", "time": "19:30"})
    snap = next(e for e in reversed(await history(reservation_call.call_id)) if e["type"] == EventType.state_reservation)
    r = snap["payload"]
    assert r["pending"] and r["booked"]["date"] == "2026-12-05"
    assert {c["field"]: (c["from"], c["to"]) for c in r["changes"]} == {
        "party_size": (4, 5),
        "date": ("2026-12-05", "2026-12-04"),
        "time": ("19:00", "19:30"),
    }
    await emit(reservation_call.call_id, EventType.confirm_requested, {"summary": "x"})
    await emit(reservation_call.call_id, EventType.confirm_received, {"via": "test"})
    await run_tool(reservation_call, "confirm_reservation", {})
    r = next(e for e in reversed(await history(reservation_call.call_id)) if e["type"] == EventType.state_reservation)["payload"]
    assert not r["pending"] and r["changes"] == [] and r["booked"]["time"] == "19:30"


async def test_expired_link_410_names_the_brand_only(order_call, http):
    raw = await mint(order_call.call_id)
    await revoke(order_call.call_id)
    r = await http.get(f"/api/s/{raw}")
    assert r.status_code == 410
    detail = r.json()["detail"]
    assert detail["brand"] == "Fieldstone Provisions" and set(detail) == {"message", "brand", "theme"}
    assert "Jamie" not in r.text and "cart" not in r.text
