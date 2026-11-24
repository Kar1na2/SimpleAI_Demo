"""Agent tools. Each one hits the DB, emits events, and returns a JSON string for the model.

Every call emits agent.tool_call {name, args, summary, ok} with a plain-English summary
for the link page's "Agent actions" tab. State-changing tools first emit state.cart or
state.reservation with the full snapshot.

Server-side guards (the model can't talk its way past these):
- place_order / confirm_reservation refuse unless confirm.received exists for the call.
- request_confirmation is idempotent: it won't re-emit while a request for the same state
  is pending. It re-emits only if the cart/reservation changed since the last request.
- send_link is allowed any time, but logs a warning if no agent turn mentioned "link".
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from app.call import state
from app.call.scenario import Scenario
from app.config import get_settings
from app.db import SessionLocal
from app.events import EventType, emit, history, last_seqs
from app.links import live_url, mask_phone, masked_url
from app.models import (
    Cart,
    CartItem,
    Order,
    OrderStatus,
    Product,
    Reservation,
    ReservationStatus,
    RestaurantSlot,
    SlotStatus,
)

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Returned to the model as an is_error tool_result. The call carries on."""


@dataclass
class ToolContext:
    call_id: uuid.UUID
    scenario: Scenario
    customer_id: int
    customer_phone: str | None
    # Auto mode: the orchestrator opens the link right after link.sent.
    on_link_sent: Callable[[], Awaitable[None]] | None = None
    end_requested: bool = False


# --- tool definitions -----------------------------------------------------------------------

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_SKU = {"type": "string", "description": "Product SKU from lookup_product, e.g. FP-RIBEYE-DA16."}
_DATE = {"type": "string", "description": "Date as YYYY-MM-DD."}

TOOL_DEFS: dict[str, dict[str, Any]] = {
    "lookup_product": {
        "description": "Search the catalog. Returns matching products with SKU, price and description. "
        "Use it before you mention any product or price.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words to search for, e.g. 'ribeye'."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "add_to_cart": {
        "description": "Add a quantity of a product to the customer's cart (on top of any already there).",
        "input_schema": {
            "type": "object",
            "properties": {"sku": _SKU, "qty": {"type": "integer", "minimum": 1, "maximum": 99}},
            "required": ["sku", "qty"],
            "additionalProperties": False,
        },
    },
    "update_cart_item": {
        "description": "Set the quantity of a product that is already in the cart. 0 removes it.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": _SKU, "qty": {"type": "integer", "minimum": 0, "maximum": 99}},
            "required": ["sku", "qty"],
            "additionalProperties": False,
        },
    },
    "remove_cart_item": {
        "description": "Remove a product from the cart.",
        "input_schema": {
            "type": "object",
            "properties": {"sku": _SKU},
            "required": ["sku"],
            "additionalProperties": False,
        },
    },
    "get_reservation": {
        "description": "Look up the caller by their caller ID and return their name and current reservation.",
        "input_schema": _NO_ARGS,
    },
    "check_availability": {
        "description": "List the dinner slots for one date, each open or full. If the caller has a reservation, "
        "also says whether their current time is open that day and the two nearest open times.",
        "input_schema": {
            "type": "object",
            "properties": {"date": _DATE},
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    "update_reservation": {
        "description": "Change the caller's reservation. Pass only what changes. The slot must be open. "
        "The reservation stays pending until the caller confirms on the link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "party_size": {"type": "integer", "minimum": 1, "maximum": 12},
                "date": _DATE,
                "time": {"type": "string", "description": "24-hour time as HH:MM, e.g. 19:30."},
            },
            "additionalProperties": False,
        },
    },
    "add_note": {
        "description": "Add a short note the customer can see on the link page: a fact or decision from the call.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "One short sentence."}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    "send_link": {
        "description": "Text the customer the live link. Only after they said yes to getting it.",
        "input_schema": _NO_ARGS,
    },
    "request_confirmation": {
        "description": "Show the Confirm screen on the customer's link with a one-line summary of what will be "
        "committed. Then ask them to check the final page and tap Confirm, and wait.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "One line, e.g. '2 x Dry-Aged Ribeye'."}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    "place_order": {
        "description": "Place the order for the current cart. Works only after the customer tapped Confirm.",
        "input_schema": _NO_ARGS,
    },
    "confirm_reservation": {
        "description": "Commit the reservation change. Works only after the customer tapped Confirm.",
        "input_schema": _NO_ARGS,
    },
    "end_call": {
        "description": "Hang up. Call it right after you say goodbye.",
        "input_schema": _NO_ARGS,
    },
}

_COMMON = ["add_note", "send_link", "request_confirmation", "end_call"]
SCENARIO_TOOLS: dict[str, list[str]] = {
    "order": ["lookup_product", "add_to_cart", "update_cart_item", "remove_cart_item", *_COMMON, "place_order"],
    "reservation": ["get_reservation", "check_availability", "update_reservation", *_COMMON, "confirm_reservation"],
}


def tool_definitions(scenario: str) -> list[dict[str, Any]]:
    """Tool list for one scenario, in a fixed order (keeps the prompt cache prefix stable)."""
    return [{"name": name, **TOOL_DEFS[name]} for name in SCENARIO_TOOLS[scenario]]


# --- arg helpers ----------------------------------------------------------------------------


def _int(args: dict[str, Any], key: str, lo: int, hi: int) -> int:
    v = args.get(key)
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ToolError(f"{key} must be a whole number from {lo} to {hi}")
    return v


def _str(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"{key} is required")
    return v.strip()


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"date must be YYYY-MM-DD, got {value!r}") from None


def _time(value: str) -> dt.time:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ToolError(f"time must be 24-hour HH:MM, got {value!r}")
    return dt.time(int(m[1]), int(m[2]))


def _model_cart(snap: dict[str, Any]) -> dict[str, Any]:
    """The cart as the model sees it: names, quantities and prices it can say aloud."""
    return {
        "items": [
            {"sku": i["sku"], "name": i["name"], "qty": i["qty"], "line_total": state.money(i["line_total_cents"])}
            for i in snap["items"]
        ],
        "subtotal": state.money(snap["subtotal_cents"]),
        "shipping": "free" if snap["free_shipping"] else state.money(snap["shipping_cents"]),
        "total": state.money(snap["total_cents"]),
        "order_status": snap["order"]["status"] if snap["order"] else "not placed",
    }


# --- order tools ----------------------------------------------------------------------------


async def _product(session, sku: str) -> Product:
    product = await session.scalar(select(Product).where(Product.sku.ilike(sku)))
    if product is None:
        raise ToolError(f"No product with SKU {sku!r}. Use lookup_product to find the SKU.")
    return product


async def _open_cart(session, ctx: ToolContext) -> Cart:
    cart = await state.call_cart(session, ctx.call_id)
    if cart is None:
        cart = Cart(customer_id=ctx.customer_id, call_id=ctx.call_id)
        session.add(cart)
        await session.flush()
        await session.refresh(cart, ["items"])
    placed = await session.scalar(
        select(Order.id).where(Order.call_id == ctx.call_id, Order.status == OrderStatus.placed)
    )
    if placed:
        raise ToolError("The order is already placed; the cart can't change.")
    return cart


async def _emit_cart(ctx: ToolContext) -> dict[str, Any]:
    async with SessionLocal() as session:
        snap = await state.cart_snapshot(session, ctx.call_id)
    await emit(ctx.call_id, EventType.state_cart, snap)
    return snap


async def lookup_product(ctx: ToolContext, args: dict[str, Any]):
    query = _str(args, "query")
    words = {w.rstrip("s") for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2}
    async with SessionLocal() as session:
        products = list(await session.scalars(select(Product).order_by(Product.id)))

    def score(p: Product) -> int:
        hay = f"{p.name} {p.description} {p.sku}".lower()
        return sum(1 for w in words if w in hay)

    ranked = sorted((p for p in products if score(p) > 0), key=score, reverse=True)
    found = ranked or products
    result = {
        "products": [
            {"sku": p.sku, "name": p.name, "price": state.money(p.price_cents), "description": p.description}
            for p in found
        ]
    }
    if not ranked:
        result["note"] = "No direct match; this is the whole catalog."
    names = ", ".join(p.name for p in found[:3])
    return result, f"Looked up “{query}”: {names}" if ranked else f"Looked up “{query}”: no match"


async def add_to_cart(ctx: ToolContext, args: dict[str, Any]):
    sku, qty = _str(args, "sku"), _int(args, "qty", 1, 99)
    async with SessionLocal() as session, session.begin():
        product = await _product(session, sku)
        cart = await _open_cart(session, ctx)
        item = next((i for i in cart.items if i.product_id == product.id), None)
        if item:
            item.quantity += qty
        else:
            session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=qty))
        name = product.name
    snap = await _emit_cart(ctx)
    return {"cart": _model_cart(snap)}, f"Added {qty} × {name} to cart"


async def update_cart_item(ctx: ToolContext, args: dict[str, Any]):
    sku, qty = _str(args, "sku"), _int(args, "qty", 0, 99)
    async with SessionLocal() as session, session.begin():
        product = await _product(session, sku)
        cart = await _open_cart(session, ctx)
        item = next((i for i in cart.items if i.product_id == product.id), None)
        if item is None:
            raise ToolError(f"{product.name} isn't in the cart. Use add_to_cart.")
        before = item.quantity
        if qty == 0:
            await session.delete(item)
        else:
            item.quantity = qty
        name = product.name
    snap = await _emit_cart(ctx)
    summary = f"Removed {name} from cart" if qty == 0 else f"Changed {name} from {before} to {qty}"
    return {"cart": _model_cart(snap)}, summary


async def remove_cart_item(ctx: ToolContext, args: dict[str, Any]):
    sku = _str(args, "sku")
    async with SessionLocal() as session, session.begin():
        product = await _product(session, sku)
        cart = await _open_cart(session, ctx)
        item = next((i for i in cart.items if i.product_id == product.id), None)
        if item is None:
            raise ToolError(f"{product.name} isn't in the cart.")
        await session.delete(item)
        name = product.name
    snap = await _emit_cart(ctx)
    return {"cart": _model_cart(snap)}, f"Removed {name} from cart"


async def place_order(ctx: ToolContext, args: dict[str, Any]):
    await _require_confirmed(ctx)
    async with SessionLocal() as session, session.begin():
        existing = await session.scalar(
            select(Order).where(Order.call_id == ctx.call_id, Order.status == OrderStatus.placed)
        )
        if existing:
            return {"status": "already_placed", "order_id": existing.id}, f"Order #{existing.id} was already placed"
        cart = await state.call_cart(session, ctx.call_id)
        if cart is None or not cart.items:
            raise ToolError("The cart is empty.")
        total = (await state.cart_snapshot(session, ctx.call_id))["total_cents"]
        order = Order(
            customer_id=ctx.customer_id,
            cart_id=cart.id,
            call_id=ctx.call_id,
            status=OrderStatus.placed,
            total_cents=total,
            placed_at=dt.datetime.now(dt.UTC),
        )
        session.add(order)
        await session.flush()
        order_id = order.id
    snap = await _emit_cart(ctx)
    return (
        {"status": "placed", "order_id": order_id, "cart": _model_cart(snap)},
        f"Placed order #{order_id} for {state.money(total)}",
    )


# --- reservation tools ----------------------------------------------------------------------


async def _caller_reservation(session, ctx: ToolContext) -> Reservation:
    customer = await state.customer_by_phone(session, ctx.customer_phone)
    if customer is None:
        raise ToolError("No guest on file for this caller ID. Ask for their name.")
    r = await state.customer_reservation(session, customer.id)
    if r is None:
        raise ToolError(f"{customer.name} has no reservation on file.")
    return r


async def _emit_reservation(ctx: ToolContext) -> dict[str, Any]:
    async with SessionLocal() as session:
        r = await _caller_reservation(session, ctx)
        snap = state.reservation_snapshot(r)
    await emit(ctx.call_id, EventType.state_reservation, snap)
    return snap


def _describe(snap: dict[str, Any]) -> str:
    d = dt.date.fromisoformat(snap["date"])
    t = dt.time.fromisoformat(snap["time"])
    return f"{snap['party_size']} people, {state.spoken_date(d)}, {state.spoken_time(t)}"


async def get_reservation(ctx: ToolContext, args: dict[str, Any]):
    # Not state-changing, but it puts the reservation on the caller's link page.
    snap = await _emit_reservation(ctx)
    return (
        {"customer_name": snap["customer_name"], "reservation": snap},
        f"Found {snap['customer_name']}'s reservation: {_describe(snap)}",
    )


def _nearest_open(slots: list[RestaurantSlot], around: dt.time, n: int = 2) -> list[dt.time]:
    def minutes(t: dt.time) -> int:
        return t.hour * 60 + t.minute

    open_times = [s.time for s in slots if s.status == SlotStatus.open and s.time != around]
    return sorted(sorted(open_times, key=lambda t: (abs(minutes(t) - minutes(around)), t))[:n])


async def check_availability(ctx: ToolContext, args: dict[str, Any]):
    day = _date(_str(args, "date"))
    async with SessionLocal() as session:
        slots = list(
            await session.scalars(
                select(RestaurantSlot).where(RestaurantSlot.date == day).order_by(RestaurantSlot.time)
            )
        )
        customer = await state.customer_by_phone(session, ctx.customer_phone)
        current = await state.customer_reservation(session, customer.id) if customer else None
    label = f"{day:%A}, {day:%B} {day.day}"
    if not slots:
        return {"date": day.isoformat(), "day": label, "slots": [], "note": "No reservations taken that day."}, (
            f"Checked {state.spoken_date(day)}: not taking reservations"
        )
    result: dict[str, Any] = {
        "date": day.isoformat(),
        "day": label,
        "slots": [{"time": state.hhmm(s.time), "status": s.status.value} for s in slots],
    }
    summary = f"Checked {state.spoken_date(day)}: {sum(s.status == SlotStatus.open for s in slots)} open times"
    if current:
        mine = next((s for s in slots if s.time == current.time), None)
        status = mine.status.value if mine else "not offered"
        nearest = _nearest_open(slots, current.time)
        result["your_time"] = {"time": state.hhmm(current.time), "status": status}
        result["nearest_open_to_your_time"] = [state.hhmm(t) for t in nearest]
        if status != "open":
            summary = (
                f"Checked {state.spoken_date(day)}: {state.spoken_time(current.time)} is {status}; "
                f"nearest open {' and '.join(state.spoken_time(t) for t in nearest)}"
            )
    return result, summary


async def update_reservation(ctx: ToolContext, args: dict[str, Any]):
    if not {"party_size", "date", "time"} & {k for k, v in args.items() if v not in (None, "")}:
        raise ToolError("Pass at least one of party_size, date, time.")
    party = _int(args, "party_size", 1, 12) if args.get("party_size") not in (None, "") else None
    new_date = _date(args["date"]) if args.get("date") else None
    new_time = _time(args["time"]) if args.get("time") else None

    async with SessionLocal() as session, session.begin():
        r = await _caller_reservation(session, ctx)
        before = state.reservation_snapshot(r)
        date, time = new_date or r.date, new_time or r.time
        if (date, time) != (r.date, r.time):
            slot = await session.scalar(
                select(RestaurantSlot).where(RestaurantSlot.date == date, RestaurantSlot.time == time)
            )
            if slot is None:
                raise ToolError(f"We don't seat at {state.hhmm(time)} on {date.isoformat()}. Use check_availability.")
            if slot.status != SlotStatus.open:
                raise ToolError(f"{state.spoken_date(date)} at {state.hhmm(time)} is full. Use check_availability.")
        await session.execute(
            update(Reservation)
            .where(Reservation.id == r.id)
            .values(
                party_size=party or r.party_size,
                date=date,
                time=time,
                status=ReservationStatus.pending_change,
            )
        )
    snap = await _emit_reservation(ctx)

    changes = []
    if snap["party_size"] != before["party_size"]:
        changes.append(f"party size from {before['party_size']} to {snap['party_size']}")
    if (snap["date"], snap["time"]) != (before["date"], before["time"]):
        changes.append(
            f"time from {_describe(before).split(', ', 1)[1]} to {_describe(snap).split(', ', 1)[1]}"
        )
    summary = "Changed " + " and ".join(changes) if changes else "Reservation unchanged"
    return {"reservation": snap, "note": "Pending until the caller confirms on the link."}, summary


async def confirm_reservation(ctx: ToolContext, args: dict[str, Any]):
    await _require_confirmed(ctx)
    async with SessionLocal() as session, session.begin():
        r = await _caller_reservation(session, ctx)
        await session.execute(
            update(Reservation)
            .where(Reservation.id == r.id)
            .values(
                status=ReservationStatus.confirmed,
                booked_party_size=r.party_size,
                booked_date=r.date,
                booked_time=r.time,
            )
        )
    snap = await _emit_reservation(ctx)
    return {"status": "confirmed", "reservation": snap}, f"Confirmed reservation: {_describe(snap)}"


# --- shared tools ---------------------------------------------------------------------------


async def add_note(ctx: ToolContext, args: dict[str, Any]):
    text = _str(args, "text")
    await emit(ctx.call_id, EventType.note_added, {"text": text})
    return {"ok": True}, f"Noted: {text}"


async def _agent_mentioned_link(call_id: uuid.UUID) -> bool:
    return any(
        e["payload"].get("speaker") == "agent" and "link" in e["payload"].get("text_said", "").lower()
        for e in await history(call_id)
        if e["type"] == EventType.transcript_turn
    )


async def send_link(ctx: ToolContext, args: dict[str, Any]):
    url = live_url(ctx.call_id)
    if url is None:
        raise ToolError("There's no active link for this call.")
    to = mask_phone(ctx.customer_phone)
    if EventType.link_sent in await last_seqs(ctx.call_id):
        return {"status": "already_sent", "to": to}, "Link was already sent"
    if not await _agent_mentioned_link(ctx.call_id):
        log.warning("call %s: send_link called before the agent offered the link", ctx.call_id)
    raw = url.rsplit("/", 1)[1]
    what = "cart" if ctx.scenario.name == "order" else "reservation"
    sms = (
        f"{ctx.scenario.brand}: here's your live link. It shows your {what} and our notes during the call "
        f"and expires in {get_settings().link_ttl_minutes} min: {masked_url(raw)}"
    )
    await emit(ctx.call_id, EventType.link_sent, {"to": to, "sms_text": sms})
    return {"status": "sent", "to": to}, f"Texted the live link to {to}"


async def request_confirmation(ctx: ToolContext, args: dict[str, Any]):
    summary_text = _str(args, "summary")
    seqs = await last_seqs(ctx.call_id)
    if EventType.link_sent not in seqs:
        raise ToolError("Send the link first: the customer confirms on the link page.")
    if EventType.confirm_received in seqs:
        return {"status": "already_confirmed"}, "The customer already confirmed"
    last_request = seqs.get(EventType.confirm_requested)
    last_change = max(seqs.get(EventType.state_cart, 0), seqs.get(EventType.state_reservation, 0))
    if last_request and last_request > last_change:
        return (
            {"status": "already_requested", "note": "Still waiting for the customer to tap Confirm on the link."},
            "Confirmation already requested",
        )
    async with SessionLocal() as session:
        if ctx.scenario.name == "order":
            snap: dict[str, Any] = {"cart": await state.cart_snapshot(session, ctx.call_id)}
        else:
            snap = {"reservation": state.reservation_snapshot(await _caller_reservation(session, ctx))}
    await emit(ctx.call_id, EventType.confirm_requested, {"summary": summary_text, **snap})
    return (
        {"status": "requested", "note": "The Confirm screen is on the customer's link now. Ask them to check it and "
         "tap Confirm, then wait for the system note."},
        f"Asked the customer to confirm: {summary_text}",
    )


async def _require_confirmed(ctx: ToolContext) -> None:
    if EventType.confirm_received not in await last_seqs(ctx.call_id):
        raise ToolError(
            "The customer hasn't tapped Confirm on the link yet. Call request_confirmation if you haven't, "
            "ask them to check the final page and tap Confirm, then wait for the system note."
        )


async def end_call(ctx: ToolContext, args: dict[str, Any]):
    ctx.end_requested = True
    return {"status": "ending"}, "Ended the call"


HANDLERS = {
    "lookup_product": lookup_product,
    "add_to_cart": add_to_cart,
    "update_cart_item": update_cart_item,
    "remove_cart_item": remove_cart_item,
    "get_reservation": get_reservation,
    "check_availability": check_availability,
    "update_reservation": update_reservation,
    "add_note": add_note,
    "send_link": send_link,
    "request_confirmation": request_confirmation,
    "place_order": place_order,
    "confirm_reservation": confirm_reservation,
    "end_call": end_call,
}


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON content for the tool_result, is_error)."""
    args = dict(args or {})
    handler = HANDLERS.get(name) if name in SCENARIO_TOOLS[ctx.scenario.name] else None
    try:
        if handler is None:
            raise ToolError(f"Unknown tool {name!r}. Available: {', '.join(SCENARIO_TOOLS[ctx.scenario.name])}.")
        result, summary = await handler(ctx, args)
    except ToolError as e:
        await emit(
            ctx.call_id,
            EventType.agent_tool_call,
            {"name": name, "args": args, "summary": f"Refused: {e}", "ok": False},
        )
        return json.dumps({"error": str(e)}), True

    payload: dict[str, Any] = {"name": name, "args": args, "summary": summary, "ok": True}
    if name == "check_availability":
        payload["data"] = result  # lets the calendar show the slots the agent just checked
    await emit(ctx.call_id, EventType.agent_tool_call, payload)
    if name == "send_link" and result.get("status") == "sent" and ctx.on_link_sent:
        await ctx.on_link_sent()
    return json.dumps(result), False
