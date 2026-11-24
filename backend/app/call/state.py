"""Snapshots of a call's cart / reservation, read from the DB.

state.cart and state.reservation events carry these full snapshots, and GET /api/s/{token}
returns them, so the link page can always render from DB state.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Cart, CartItem, Customer, Order, Reservation, RestaurantSlot

# Fieldstone Provisions store rule (the order system prompt says the same).
FREE_SHIPPING_CENTS = 15000


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def hhmm(t: dt.time) -> str:
    return t.strftime("%H:%M")


def spoken_time(t: dt.time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")  # 7:30 PM


def spoken_date(d: dt.date) -> str:
    return f"{d:%a %b} {d.day}"  # Fri Dec 4


async def call_cart(session: AsyncSession, call_id: uuid.UUID) -> Cart | None:
    return await session.scalar(
        select(Cart)
        .where(Cart.call_id == call_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
        .order_by(Cart.id.desc())
        .limit(1)
    )


async def cart_snapshot(session: AsyncSession, call_id: uuid.UUID) -> dict[str, Any]:
    cart = await call_cart(session, call_id)
    items = []
    for item in sorted(cart.items, key=lambda i: i.id) if cart else []:
        p = item.product
        items.append(
            {
                "sku": p.sku,
                "name": p.name,
                "qty": item.quantity,
                "unit_price_cents": p.price_cents,
                "line_total_cents": p.price_cents * item.quantity,
                "image_path": p.image_path,
            }
        )
    subtotal = sum(i["line_total_cents"] for i in items)
    order = await session.scalar(
        select(Order).where(Order.call_id == call_id).order_by(Order.id.desc()).limit(1)
    )
    return {
        "items": items,
        "item_count": sum(i["qty"] for i in items),
        "subtotal_cents": subtotal,
        "free_shipping_threshold_cents": FREE_SHIPPING_CENTS,
        "free_shipping": subtotal >= FREE_SHIPPING_CENTS,
        "order": (
            {"id": order.id, "status": order.status.value, "total_cents": order.total_cents}
            if order
            else None
        ),
    }


async def customer_reservation(session: AsyncSession, customer_id: int) -> Reservation | None:
    return await session.scalar(
        select(Reservation)
        .where(Reservation.customer_id == customer_id)
        .options(selectinload(Reservation.customer))
        .order_by(Reservation.id.desc())
        .limit(1)
    )


def reservation_snapshot(r: Reservation) -> dict[str, Any]:
    return {
        "id": r.id,
        "customer_name": r.customer.name,
        "party_size": r.party_size,
        "date": r.date.isoformat(),
        "weekday": f"{r.date:%A}",
        "time": hhmm(r.time),
        "status": r.status.value,
    }


async def slots_snapshot(session: AsyncSession) -> list[dict[str, str]]:
    rows = await session.scalars(select(RestaurantSlot).order_by(RestaurantSlot.date, RestaurantSlot.time))
    return [{"date": s.date.isoformat(), "time": hhmm(s.time), "status": s.status.value} for s in rows]


async def customer_by_phone(session: AsyncSession, phone: str | None) -> Customer | None:
    if not phone:
        return None
    return await session.scalar(select(Customer).where(Customer.phone == phone))
