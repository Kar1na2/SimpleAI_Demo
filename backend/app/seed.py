"""Wipe and reseed the demo database.

    uv run python -m app.seed

Idempotent: drops every table (call sessions and events too), recreates the schema and
loads the fixed demo data below. Running it twice gives the same state.
"""

import asyncio
import datetime as dt

from sqlalchemy import text

from app.db import Base, SessionLocal, engine
from app.models import (
    Customer,
    Lead,
    Product,
    Reservation,
    RestaurantSlot,
    SlotStatus,
)

# ======================================================================================
# SCENARIO DATA. Source of truth: CLAUDE.md > Scenarios ("today" is Tue 2026-11-24).
# [CLAUDE.md] = fixed by CLAUDE.md or the build prompt. Everything else (prices, copy,
# the 2 extra SKUs, phone numbers, the dinner-slot grid) is chosen here: fictional
# 555-01xx numbers and example.com emails only.
# ======================================================================================

# --- Fieldstone Provisions: premium meat delivery, Scenario A (outbound) ---
PRODUCTS = [
    dict(  # [CLAUDE.md] the lead's product; "two" misheard as "ten"
        sku="FP-RIBEYE-DA16",
        name="Dry-Aged Ribeye",
        description="16 oz ribeye, dry-aged 45 days for a deep, nutty flavor.",
        # 2 ribeyes + butter = $156, over the $150 free-shipping line, so the agent has no
        # reason for a second upsell.
        price_cents=7200,
        image_path="/products/ribeye.svg",
    ),
    dict(  # [CLAUDE.md]
        sku="FP-FILET-8",
        name="Filet Mignon",
        description="8 oz center-cut tenderloin, hand-tied. Lean and buttery-tender.",
        price_cents=5400,
        image_path="/products/filet.svg",
    ),
    dict(  # [CLAUDE.md] the upsell add-on
        sku="FP-BUTTER-GH",
        name="Garlic-Herb Compound Butter",
        description="Cultured butter with roasted garlic, parsley and thyme. 4 oz log.",
        price_cents=1200,
        image_path="/products/garlic-herb-butter.svg",
    ),
    dict(  # [CLAUDE.md]
        sku="FP-GIFTBOX",
        name="Steakhouse Gift Box",
        description="Two dry-aged ribeyes, two filets, compound butter and smoked salt in a keepsake box.",
        price_cents=21900,
        image_path="/products/gift-box.svg",
    ),
    dict(
        sku="FP-STRIP-14",
        name="New York Strip",
        description="14 oz strip loin with a firm bite and a clean fat cap.",
        price_cents=4200,
        image_path="/products/ny-strip.svg",
    ),
    dict(
        sku="FP-SALT-SMOKED",
        name="Smoked Flaky Sea Salt",
        description="Oak-smoked finishing salt. 3 oz jar.",
        price_cents=1400,
        image_path="/products/smoked-salt.svg",
    ),
]

# [CLAUDE.md] Jamie Park showed interest in the ribeye (lead record). Cart starts empty, so no
# cart is seeded: the agent's add_to_cart creates one for the call.
# Customer ids are fixed because scenarios/*.yaml reference them (meta.customer_id).
JAMIE_ID = 1
JAMIE = dict(id=JAMIE_ID, name="Jamie Park", phone="+15555550142", email="jamie.park@example.com")
JAMIE_LEAD = dict(product_sku="FP-RIBEYE-DA16", note="Interested in the dry-aged ribeye.")

# --- Juniper Table: restaurant, Scenario B (inbound) ---
SLOT_START = dt.date(2026, 11, 30)  # [CLAUDE.md] Mon
SLOT_END = dt.date(2026, 12, 13)  # [CLAUDE.md] Sun, inclusive
SLOT_TIMES = [dt.time(h, m) for h in range(17, 22) for m in (0, 30)][:-1]  # 17:00-21:00 every 30 min
FULL_SLOTS = {(dt.date(2026, 12, 4), dt.time(19, 0))}  # [CLAUDE.md] Fri 19:00 full; 18:30 + 19:30 open

# [CLAUDE.md] Daniel Cho calls in. The agent finds him by caller phone number.
DANIEL_ID = 2
DANIEL = dict(id=DANIEL_ID, name="Daniel Cho", phone="+15555550187", email="daniel.cho@example.com")
DANIEL_RESERVATION = dict(party_size=4, date=dt.date(2026, 12, 5), time=dt.time(19, 0))  # [CLAUDE.md] Sat
# ======================================================================================


def _slot_rows() -> list[RestaurantSlot]:
    rows = []
    day = SLOT_START
    while day <= SLOT_END:
        for t in SLOT_TIMES:
            status = SlotStatus.full if (day, t) in FULL_SLOTS else SlotStatus.open
            rows.append(RestaurantSlot(date=day, time=t, status=status))
        day += dt.timedelta(days=1)
    return rows


async def seed() -> dict[str, int]:
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session, session.begin():
        products = {p["sku"]: Product(**p) for p in PRODUCTS}
        session.add_all(products.values())

        jamie = Customer(**JAMIE)
        daniel = Customer(**DANIEL)
        session.add_all([jamie, daniel])
        await session.flush()
        # Explicit ids above: move the sequence past them.
        await session.execute(
            text("SELECT setval(pg_get_serial_sequence('customers', 'id'), (SELECT max(id) FROM customers))")
        )
        session.add(Lead(customer=jamie, product=products[JAMIE_LEAD["product_sku"]], note=JAMIE_LEAD["note"]))

        slots = _slot_rows()
        session.add_all(slots)
        session.add(Reservation.booked(daniel.id, **DANIEL_RESERVATION))

    return {"products": len(PRODUCTS), "customers": 2, "leads": 1, "slots": len(slots), "reservations": 1}


async def main() -> None:
    counts = await seed()
    await engine.dispose()
    print("Seeded: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    asyncio.run(main())
