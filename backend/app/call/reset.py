"""Put one scenario's mutable data back to the seed state before a call.

Only the scenario customer's cart/order/reservation rows are touched. Earlier calls and their
events stay, so past runs remain replayable.
"""

from __future__ import annotations

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import Cart, Order, Reservation, ReservationStatus
from app.seed import DANIEL_RESERVATION


async def reset_scenario_data(scenario: str, customer_id: int) -> None:
    async with SessionLocal() as session, session.begin():
        if scenario == "order":
            # Cart starts empty (CLAUDE.md). cart_items go with their cart (ON DELETE CASCADE).
            await session.execute(delete(Order).where(Order.customer_id == customer_id))
            await session.execute(delete(Cart).where(Cart.customer_id == customer_id))
        elif scenario == "reservation":
            # Back to party of 4, Sat 2026-12-05 19:00, booked.
            await session.execute(delete(Reservation).where(Reservation.customer_id == customer_id))
            session.add(
                Reservation(customer_id=customer_id, status=ReservationStatus.booked, **DANIEL_RESERVATION)
            )
