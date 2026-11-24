"""Server-side guards on the agent's commit tools, and request_confirmation idempotency."""

import json

from sqlalchemy import select

from app.call.tools import run_tool
from app.db import SessionLocal
from app.events import EventType, emit, history
from app.links import live_url, mint
from app.models import Order, Reservation, ReservationStatus


async def _types(call_id) -> list[str]:
    return [e["type"] for e in await history(call_id)]


async def _send_link(ctx) -> None:
    await mint(ctx.call_id)
    content, is_error = await run_tool(ctx, "send_link", {})
    assert not is_error, content


async def test_place_order_refuses_without_confirm_received(order_call, http):
    ctx = order_call
    await run_tool(ctx, "add_to_cart", {"sku": "FP-RIBEYE-DA16", "qty": 2})

    content, is_error = await run_tool(ctx, "place_order", {})
    assert is_error and "hasn't tapped Confirm" in json.loads(content)["error"]
    async with SessionLocal() as s:
        assert await s.scalar(select(Order).where(Order.call_id == ctx.call_id)) is None

    # Requesting confirmation alone is not enough either.
    await _send_link(ctx)
    _, is_error = await run_tool(ctx, "request_confirmation", {"summary": "2 x ribeye"})
    assert not is_error
    _, is_error = await run_tool(ctx, "place_order", {})
    assert is_error

    # The real round trip: confirm.received via the public endpoint, then it works.
    token = live_url(ctx.call_id).rsplit("/", 1)[1]
    assert (await http.post(f"/api/s/{token}/confirm")).status_code == 200
    content, is_error = await run_tool(ctx, "place_order", {})
    assert not is_error, content
    assert json.loads(content)["status"] == "placed"
    refused = [e for e in await history(ctx.call_id) if e["type"] == EventType.agent_tool_call and not e["payload"]["ok"]]
    assert [e["payload"]["name"] for e in refused] == ["place_order", "place_order"]


async def test_confirm_reservation_refuses_without_confirm_received(reservation_call):
    ctx = reservation_call
    await run_tool(ctx, "update_reservation", {"party_size": 5})

    content, is_error = await run_tool(ctx, "confirm_reservation", {})
    assert is_error and "hasn't tapped Confirm" in json.loads(content)["error"]
    async with SessionLocal() as s:
        r = await s.scalar(select(Reservation).where(Reservation.customer_id == ctx.customer_id))
        assert r.status == ReservationStatus.pending_change

    await emit(ctx.call_id, EventType.confirm_requested, {"summary": "test"})
    await emit(ctx.call_id, EventType.confirm_received, {"via": "test"})
    _, is_error = await run_tool(ctx, "confirm_reservation", {})
    assert not is_error
    async with SessionLocal() as s:
        r = await s.scalar(select(Reservation).where(Reservation.customer_id == ctx.customer_id))
        assert r.status == ReservationStatus.confirmed


async def test_request_confirmation_is_idempotent_until_state_changes(order_call):
    ctx = order_call
    await run_tool(ctx, "add_to_cart", {"sku": "FP-RIBEYE-DA16", "qty": 2})
    content, is_error = await run_tool(ctx, "request_confirmation", {"summary": "x"})
    assert is_error and "Send the link first" in content

    await _send_link(ctx)
    for _ in range(3):
        await run_tool(ctx, "request_confirmation", {"summary": "2 x ribeye"})
    assert (await _types(ctx.call_id)).count(EventType.confirm_requested) == 1

    # The cart changes after the request: a new request shows the new cart.
    await run_tool(ctx, "add_to_cart", {"sku": "FP-BUTTER-GH", "qty": 1})
    content, _ = await run_tool(ctx, "request_confirmation", {"summary": "2 x ribeye + butter"})
    assert json.loads(content)["status"] == "requested"
    assert (await _types(ctx.call_id)).count(EventType.confirm_requested) == 2


async def test_reservation_update_refuses_full_slot_and_reports_nearest(reservation_call):
    ctx = reservation_call
    content, is_error = await run_tool(ctx, "update_reservation", {"date": "2026-12-04", "time": "19:00"})
    assert is_error and "full" in content
    content, _ = await run_tool(ctx, "check_availability", {"date": "2026-12-04"})
    data = json.loads(content)
    assert data["your_time"] == {"time": "19:00", "status": "full"}
    assert data["nearest_open_to_your_time"] == ["18:30", "19:30"]


async def test_tool_events_have_plain_english_summaries(order_call):
    ctx = order_call
    await run_tool(ctx, "add_to_cart", {"sku": "FP-RIBEYE-DA16", "qty": 10})
    await run_tool(ctx, "update_cart_item", {"sku": "FP-RIBEYE-DA16", "qty": 2})
    summaries = [e["payload"]["summary"] for e in await history(ctx.call_id) if e["type"] == EventType.agent_tool_call]
    assert summaries == ["Added 10 × Dry-Aged Ribeye to cart", "Changed Dry-Aged Ribeye from 10 to 2"]
    carts = [e["payload"] for e in await history(ctx.call_id) if e["type"] == EventType.state_cart]
    assert [i["qty"] for i in carts[-1]["items"]] == [2]
