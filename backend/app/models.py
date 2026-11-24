import enum
import uuid
import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def str_enum(cls: type[enum.StrEnum]) -> Enum:
    """Store enum *values* as VARCHAR + CHECK (not a native PG enum), so create_all/drop_all
    stay painless and adding a value later is just a reseed."""
    return Enum(
        cls,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
        length=32,
        validate_strings=True,
    )


def created_at_col() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OrderStatus(enum.StrEnum):
    draft = "draft"
    placed = "placed"


class SlotStatus(enum.StrEnum):
    open = "open"
    full = "full"


class ReservationStatus(enum.StrEnum):
    booked = "booked"
    pending_change = "pending_change"
    confirmed = "confirmed"


class CallDirection(enum.StrEnum):
    inbound = "inbound"  # customer calls the business (Scenario B)
    outbound = "outbound"  # agent calls the customer (Scenario A)


class CallStatus(enum.StrEnum):
    """The `call.status` event values, plus `created` for a session that hasn't started dialing."""

    created = "created"
    dialing = "dialing"
    ringing = "ringing"
    connected = "connected"
    ended = "ended"


# --- Commerce (Fieldstone Provisions) ---------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[dt.datetime] = created_at_col()


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str] = mapped_column(String(255))  # served by the frontend, e.g. /products/ribeye.svg


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))  # product interest
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_at_col()

    customer: Mapped[Customer] = relationship()
    product: Mapped[Product] = relationship()


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = created_at_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["CartItem"]] = relationship(back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    cart_id: Mapped[int | None] = mapped_column(ForeignKey("carts.id", ondelete="SET NULL"))
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id", ondelete="SET NULL"))
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus), default=OrderStatus.draft)
    total_cents: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[dt.datetime] = created_at_col()
    placed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# --- Restaurant (Juniper Table) ---------------------------------------------------------


class RestaurantSlot(Base):
    __tablename__ = "restaurant_slots"
    __table_args__ = (UniqueConstraint("date", "time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    time: Mapped[dt.time] = mapped_column(Time)
    status: Mapped[SlotStatus] = mapped_column(str_enum(SlotStatus), default=SlotStatus.open)


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    party_size: Mapped[int] = mapped_column(Integer)
    date: Mapped[dt.date] = mapped_column(Date)
    time: Mapped[dt.time] = mapped_column(Time)
    status: Mapped[ReservationStatus] = mapped_column(
        str_enum(ReservationStatus), default=ReservationStatus.booked
    )
    created_at: Mapped[dt.datetime] = created_at_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship()


# --- Calls, link tokens, event log ------------------------------------------------------


class CallSession(Base):
    __tablename__ = "call_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scenario: Mapped[str] = mapped_column(String(64))
    direction: Mapped[CallDirection] = mapped_column(str_enum(CallDirection))
    status: Mapped[CallStatus] = mapped_column(str_enum(CallStatus), default=CallStatus.created)
    # The customer's number: caller ID on inbound calls, the dialed number on outbound ones.
    # Scenario B's agent finds the customer and reservation from this ("look it up by caller phone").
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    # Per-call event counter. app.events.emit bumps it with UPDATE ... RETURNING, so seq
    # assignment is atomic even if several workers emit for the same call.
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[dt.datetime] = created_at_col()
    connected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class LinkToken(Base):
    __tablename__ = "link_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)  # sha256 hex of the raw token
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = created_at_col()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class CallEvent(Base):
    __tablename__ = "call_events"
    __table_args__ = (UniqueConstraint("call_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call_sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = created_at_col()
