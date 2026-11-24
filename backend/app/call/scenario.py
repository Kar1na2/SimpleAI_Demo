"""Scenario files and beat `wait_for` evaluation.

A scenario is backend/scenarios/<name>.yaml (meta + ordered customer beats) plus its
system prompt in <name>.prompt.md. Beat fields:

  id          unique beat id
  say         what the customer actually says (TTS speaks this; it's the truth)
  heard_as    optional: what the agent hears instead (the mishear)
  corrects    optional: an earlier beat id. Speaking this beat emits transcript.correction
              for that beat's turn, with corrected_text = that beat's `say`
  wait_for    optional, every listed condition must hold before the customer speaks:
                agent_said_matches  case-insensitive regex, searched in everything the agent
                                    said since the previous customer beat (its latest turn)
                event               an event type emitted since the previous customer beat
                state               "cart.<SKU>.qty <op> <n>" or "reservation.<field> <op> <v>"
                                    (op: == != >= <= > <). A cart item that isn't in the cart,
                                    or a reservation not yet on screen, never matches.
  action      optional: "confirm" (auto mode POSTs the link's confirm endpoint; manual
              mode waits for a real click)
  optional    true: skip this beat if the call has already ended

The orchestrator evaluates wait_for against a CallView, built only from call_events.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.events import EventType

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"

Direction = Literal["inbound", "outbound"]


class ScenarioError(ValueError):
    pass


# --- state predicates ---------------------------------------------------------------------

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}
_STATE_RE = re.compile(
    r"^\s*(?:cart\.(?P<sku>[A-Za-z0-9_-]+)\.qty|reservation\.(?P<field>[a-z_]+))"
    r"\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<value>\S.*?)\s*$"
)


@dataclass(frozen=True)
class StatePredicate:
    raw: str
    kind: Literal["cart", "reservation"]
    key: str  # SKU for cart, field name for reservation
    op: str
    value: int | str

    @classmethod
    def parse(cls, raw: str) -> StatePredicate:
        m = _STATE_RE.match(raw)
        if not m:
            raise ScenarioError(
                f"bad state predicate {raw!r}; expected 'cart.<SKU>.qty <op> <n>' or 'reservation.<field> <op> <v>'"
            )
        value: int | str = m["value"].strip("\"'")
        if re.fullmatch(r"-?\d+", value):
            value = int(value)
        if m["sku"]:
            if not isinstance(value, int):
                raise ScenarioError(f"cart quantity must be an integer in {raw!r}")
            return cls(raw, "cart", m["sku"], m["op"], value)
        return cls(raw, "reservation", m["field"], m["op"], value)

    def holds(self, cart: dict[str, Any] | None, reservation: dict[str, Any] | None) -> bool:
        if self.kind == "cart":
            items = (cart or {}).get("items", [])
            item = next((i for i in items if i["sku"].upper() == self.key.upper()), None)
            if item is None:
                return False
            actual: Any = item["qty"]
        else:
            if not reservation or self.key not in reservation:
                return False
            actual = reservation[self.key]
        if isinstance(self.value, int) and isinstance(actual, int):
            return _OPS[self.op](actual, self.value)
        return _OPS[self.op](str(actual), str(self.value))


# --- beats ------------------------------------------------------------------------------


@dataclass
class BeatView:
    """What the orchestrator knows when deciding whether the next beat may be spoken."""

    agent_text: str = ""  # everything the agent said since the previous customer beat
    events: set[str] = field(default_factory=set)  # event types since the previous customer beat
    cart: dict[str, Any] | None = None  # latest state.cart snapshot
    reservation: dict[str, Any] | None = None  # latest state.reservation snapshot


@dataclass(frozen=True)
class WaitFor:
    agent_said_matches: re.Pattern[str] | None = None
    event: str | None = None
    state: StatePredicate | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any], beat_id: str) -> WaitFor:
        unknown = set(raw) - {"agent_said_matches", "event", "state"}
        if unknown:
            raise ScenarioError(f"beat {beat_id}: unknown wait_for keys {sorted(unknown)}")
        pattern = None
        if "agent_said_matches" in raw:
            try:
                pattern = re.compile(str(raw["agent_said_matches"]), re.IGNORECASE)
            except re.error as e:
                raise ScenarioError(f"beat {beat_id}: bad regex: {e}") from None
        event = raw.get("event")
        if event is not None and event not in {t.value for t in EventType}:
            raise ScenarioError(f"beat {beat_id}: unknown event type {event!r}")
        state = StatePredicate.parse(str(raw["state"])) if "state" in raw else None
        if pattern is None and event is None and state is None:
            raise ScenarioError(f"beat {beat_id}: empty wait_for")
        return cls(pattern, event, state)

    def unmet(self, view: BeatView) -> list[str]:
        """Conditions that don't hold yet, in plain words. Empty means the beat may be spoken."""
        out = []
        if self.agent_said_matches and not self.agent_said_matches.search(view.agent_text):
            out.append(f"agent_said_matches /{self.agent_said_matches.pattern}/")
        if self.event and self.event not in view.events:
            out.append(f"event {self.event} since the previous beat")
        if self.state and not self.state.holds(view.cart, view.reservation):
            out.append(f"state {self.state.raw}")
        return out


@dataclass(frozen=True)
class Beat:
    id: str
    say: str
    heard_as: str | None = None
    corrects: str | None = None
    wait_for: WaitFor | None = None
    action: Literal["confirm"] | None = None
    optional: bool = False

    @property
    def heard(self) -> str:
        return self.heard_as or self.say

    def unmet(self, view: BeatView) -> list[str]:
        return self.wait_for.unmet(view) if self.wait_for else []


@dataclass(frozen=True)
class Scenario:
    name: str
    brand: str
    agent_name: str
    direction: Direction
    customer_id: int
    customer_phone: str
    system_prompt: str
    beats: tuple[Beat, ...]

    def beat(self, beat_id: str) -> Beat:
        return next(b for b in self.beats if b.id == beat_id)


_BEAT_KEYS = {"id", "say", "heard_as", "corrects", "wait_for", "action", "optional"}
_META_KEYS = {"brand", "agent_name", "direction", "customer_id", "customer_phone"}


def parse_scenario(name: str, data: dict[str, Any], system_prompt: str) -> Scenario:
    meta = data.get("meta") or {}
    missing = _META_KEYS - set(meta)
    if missing:
        raise ScenarioError(f"{name}: meta is missing {sorted(missing)}")
    if meta["direction"] not in ("inbound", "outbound"):
        raise ScenarioError(f"{name}: direction must be inbound or outbound")

    beats: list[Beat] = []
    for raw in data.get("beats") or []:
        bid = raw.get("id")
        if not bid or not raw.get("say"):
            raise ScenarioError(f"{name}: every beat needs id and say ({raw!r})")
        unknown = set(raw) - _BEAT_KEYS
        if unknown:
            raise ScenarioError(f"{name}: beat {bid} has unknown keys {sorted(unknown)}")
        if any(b.id == bid for b in beats):
            raise ScenarioError(f"{name}: duplicate beat id {bid}")
        corrects = raw.get("corrects")
        if corrects is not None and not any(b.id == corrects for b in beats):
            raise ScenarioError(f"{name}: beat {bid} corrects {corrects!r}, which isn't an earlier beat")
        action = raw.get("action")
        if action not in (None, "confirm"):
            raise ScenarioError(f"{name}: beat {bid} has unknown action {action!r}")
        beats.append(
            Beat(
                id=bid,
                say=raw["say"],
                heard_as=raw.get("heard_as"),
                corrects=corrects,
                wait_for=WaitFor.parse(raw["wait_for"], bid) if raw.get("wait_for") else None,
                action=action,
                optional=bool(raw.get("optional", False)),
            )
        )
    if not beats:
        raise ScenarioError(f"{name}: no beats")

    return Scenario(
        name=name,
        brand=meta["brand"],
        agent_name=meta["agent_name"],
        direction=meta["direction"],
        customer_id=int(meta["customer_id"]),
        customer_phone=str(meta["customer_phone"]),
        system_prompt=system_prompt.strip(),
        beats=tuple(beats),
    )


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


def load_scenario(name: str) -> Scenario:
    if name not in list_scenarios():
        raise ScenarioError(f"unknown scenario {name!r}; have {list_scenarios()}")
    data = yaml.safe_load((SCENARIO_DIR / f"{name}.yaml").read_text())
    prompt = (SCENARIO_DIR / f"{name}.prompt.md").read_text()
    return parse_scenario(name, data, prompt)


# --- the orchestrator's view of a call ----------------------------------------------------


class CallView:
    """Folds call_events into what beat evaluation needs. Feed it events in seq order."""

    def __init__(self) -> None:
        self.beat = BeatView()
        self.last_seq = 0
        self.ended = False

    def apply(self, event: dict[str, Any]) -> None:
        if event["seq"] <= self.last_seq:
            return
        self.last_seq = event["seq"]
        etype, payload = event["type"], event["payload"]
        if etype == EventType.transcript_turn:
            if payload.get("speaker") == "customer":
                self.beat.agent_text = ""
                self.beat.events = set()
            else:
                self.beat.agent_text = f"{self.beat.agent_text} {payload.get('text_said', '')}".strip()
        elif etype == EventType.state_cart:
            self.beat.cart = payload
        elif etype == EventType.state_reservation:
            self.beat.reservation = payload
        elif etype == EventType.call_status and payload.get("status") == "ended":
            self.ended = True
        self.beat.events.add(etype)
