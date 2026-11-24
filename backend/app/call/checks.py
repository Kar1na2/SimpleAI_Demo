"""End-state checks and run files for live calls.

record_run() runs when every live call ends (CLI or API) and writes
backend/runs/<scenario>-<YYYYmmdd-HHMMSS>.json: every event, the checks, the silence nudges,
LLM usage, and an audio manifest (clip hash -> model, voice, instructions, text, duration)
so a replay can play or regenerate each turn's audio.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from app.call.state import customer_reservation
from app.db import SessionLocal
from app.events import Event, EventType, history
from app.models import LinkToken, Order, OrderStatus

if TYPE_CHECKING:
    from app.call.orchestrator import CallRun

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"
RIBEYE, BUTTER = "FP-RIBEYE-DA16", "FP-BUTTER-GH"

# Words the agent must not use before the customer taps Confirm (prompt rule).
COMMIT_WORDS = re.compile(r"\b(all set|set|booked|placed|done|confirmed)\b", re.IGNORECASE)

Check = tuple[str, bool, str]


async def end_state_check(run: CallRun, events: list[Event]) -> list[Check]:
    checks: list[Check] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    def first_seq(etype: str, **match: Any) -> int | None:
        return next(
            (e["seq"] for e in events if e["type"] == etype and all(e["payload"].get(k) == v for k, v in match.items())),
            None,
        )

    turns = [e for e in events if e["type"] == EventType.transcript_turn]
    confirmed = first_seq(EventType.confirm_received)

    check("call finished without error", run.error is None, run.error or "")
    spoke_after = any(t["payload"].get("speaker") == "agent" and t["seq"] > (confirmed or 0) for t in turns)
    check("agent spoke its wrap-up after the Confirm click (no silent hang-up)", bool(confirmed and spoke_after))
    early = [
        t["payload"]["text_said"]
        for t in turns
        if t["payload"].get("speaker") == "agent"
        and (confirmed is None or t["seq"] < confirmed)
        and COMMIT_WORDS.search(t["payload"]["text_said"])
    ]
    check(
        "no 'set/booked/placed/done/confirmed' before the Confirm click",
        not early,
        " | ".join(early)[:300],
    )

    async with SessionLocal() as session:
        if run.scenario.name == "order":
            order = await session.scalar(select(Order).where(Order.call_id == run.call_id))
            check("order placed", bool(order and order.status == OrderStatus.placed),
                  f"status={order.status.value if order else None}")
            cart = next((e["payload"] for e in reversed(events) if e["type"] == EventType.state_cart), {"items": []})
            got = {i["sku"]: i["qty"] for i in cart["items"]}
            check("cart is exactly 2 × ribeye + 1 × compound butter", got == {RIBEYE: 2, BUTTER: 1}, json.dumps(got))
        else:
            r = await customer_reservation(session, run.scenario.customer_id)
            got = (r.status.value, r.party_size, r.date.isoformat(), r.time.strftime("%H:%M")) if r else None
            check("reservation confirmed, party 5, 2026-12-04 19:30",
                  got == ("confirmed", 5, "2026-12-04", "19:30"), str(got))
            requests = sum(1 for e in events if e["type"] == EventType.confirm_requested)
            check("exactly one confirm.requested", requests == 1, f"got {requests}")
            check("zero silence nudges", not run.nudges, f"before {', '.join(run.nudges)}")
        token = await session.scalar(select(LinkToken).where(LinkToken.call_id == run.call_id))

    for beat in run.scenario.beats:  # every scripted correction landed on the misheard turn
        if beat.corrects:
            turn = next((t["payload"]["turn_id"] for t in turns if t["payload"].get("beat_id") == beat.corrects), None)
            check(
                f"transcript.correction exists for {beat.corrects}",
                turn is not None and first_seq(EventType.transcript_correction, turn_id=turn) is not None,
                f"{beat.corrects} turn_id={turn}",
            )

    connected = first_seq(EventType.call_status, status="connected")
    minted = first_seq(EventType.link_minted)
    check("link minted right after connected", bool(connected and minted and minted == connected + 1),
          f"connected seq={connected}, minted seq={minted}")
    yes_beat = next((b.id for b in run.scenario.beats if b.marks == "link_accepted"), None)
    yes = first_seq(EventType.transcript_turn, beat_id=yes_beat) if yes_beat else None
    sent = first_seq(EventType.link_sent)
    check(f"link sent after the customer said yes ({yes_beat})", bool(yes and sent and sent > yes),
          f"yes seq={yes}, sent seq={sent}")
    check("token revoked (DB + link.revoked event)",
          bool(token and token.revoked_at and first_seq(EventType.link_revoked)))
    if run.raw_token:
        from app.main import app  # late import: app.main imports the routers that import the orchestrator

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://link.local") as http:
            status = (await http.get(f"/api/s/{run.raw_token}")).status_code
        check("GET /api/s/{token} returns 410 after the call", status == 410, f"got {status}")
    else:
        check("GET /api/s/{token} returns 410 after the call", False, "no token was minted")
    return checks


async def record_run(run: CallRun) -> Path:
    """Check the finished call and write its run file. Sets run.checks / run.passed / run.run_file."""
    events = await history(run.call_id)
    run.checks = await end_state_check(run, events)
    run.passed = all(ok for _, ok, _ in run.checks)
    agent = run.agent
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{run.scenario.name}-{run.started_at:%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "scenario": run.scenario.name,
                "call_id": str(run.call_id),
                "mode": run.mode,
                "speed": run.speed,
                "tts": run.tts is not None,
                "started_at": run.started_at.isoformat(),
                "duration_s": round(run.duration or 0, 2),
                "passed": run.passed,
                "error": run.error,
                "nudges": run.nudges,
                "checks": [{"label": label, "ok": ok, "detail": d} for label, ok, d in run.checks],
                "usage": agent.usage.as_dict(agent.model) if agent else None,
                "unspoken_notes": agent.unspoken if agent else [],
                "audio": run.audio,
                "events": events,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    run.run_file = path.name
    return path
