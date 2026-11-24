"""Run one scenario end to end in this process, print the live transcript, check the end state.

    uv run python -m app.call.run order [--speed 4]
    uv run python -m app.call.run reservation --manual    # waits up to 180s for a real Confirm click

Auto mode needs only Postgres and ANTHROPIC_API_KEY. Manual mode also needs `make backend`
running, so the Confirm click has an endpoint to hit. This process polls the DB for the click.

Writes every event to backend/runs/<scenario>-<timestamp>.json. Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.call.orchestrator import CallRun, start_call
from app.call.state import customer_reservation, money
from app.db import SessionLocal, engine, init_db
from app.events import Event, EventType, history
from app.models import LinkToken, Order, OrderStatus

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"

RIBEYE, BUTTER = "FP-RIBEYE-DA16", "FP-BUTTER-GH"
YES_BEAT = "c2"  # the beat where the customer agrees to the link, in both scenarios

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


DIM, RED, GREEN, YELLOW, CYAN, MAGENTA, BOLD = "2", "31", "32", "33", "36", "35", "1"
PAD = "      "


def _args(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def format_event(e: Event, raw_token: str | None) -> str | None:
    t, p = e["type"], e["payload"]
    if t == EventType.call_status:
        extra = f" ({p['reason']})" if p.get("reason") else ""
        err = f"\n{PAD}{c(RED, p['error'])}" if p.get("error") else ""
        return c(DIM, f"── {p.get('status')}{extra}") + err
    if t == EventType.transcript_turn:
        if p.get("speaker") == "agent":
            return f"{c(CYAN + ';' + BOLD, p.get('name', 'Agent'))}: {p['text_said']}"
        name = c(YELLOW + ";" + BOLD, p.get("name", "Customer").split()[0])
        line = f"{name}: {p['text_said']} " + c(DIM, f"[{p.get('beat_id')}]")
        if p.get("text_heard") != p.get("text_said"):
            line += f"\n{PAD}" + c(RED, "agent heard: " + p["text_heard"])
        return line
    if t == EventType.transcript_correction:
        return PAD + c(MAGENTA, f"correction for turn {p['turn_id']}: {p['corrected_text']}")
    if t == EventType.agent_tool_call:
        color = DIM if p.get("ok") else RED
        return PAD + c(color, f"⚙ {p['name']}({_args(p.get('args', {}))}) → {p['summary']}")
    if t == EventType.state_cart:
        items = ", ".join(f"{i['qty']} × {i['name']}" for i in p["items"]) or "empty"
        order = f" · order {p['order']['status']}" if p.get("order") else ""
        return PAD + c(GREEN, f"cart: {items} · subtotal {money(p['subtotal_cents'])}{order}")
    if t == EventType.state_reservation:
        r = f"reservation: party {p['party_size']} · {p['weekday']} {p['date']} {p['time']} · {p['status']}"
        return PAD + c(GREEN, r)
    if t == EventType.link_minted:
        return c(DIM, f"{PAD}[link] minted #{p['token_id']}, expires {p['expires_at'][11:19]} UTC")
    if t == EventType.link_sent:
        return c(DIM, f"{PAD}[link] sent to {p['to']}: {p['sms_text']}")
    if t == EventType.link_opened:
        return c(DIM, f"{PAD}[link] opened")
    if t == EventType.link_revoked:
        return c(DIM, f"{PAD}[link] revoked")
    if t == EventType.confirm_requested:
        return f"{PAD}{c(MAGENTA, '[confirm] requested: ' + p.get('summary', ''))}"
    if t == EventType.confirm_received:
        return f"{PAD}{c(MAGENTA, '[confirm] received: the customer tapped Confirm')}"
    if t == EventType.note_added:
        return None  # already shown by its add_note tool call
    return c(DIM, f"{PAD}{t} {json.dumps(p)}")


async def print_live(run: CallRun) -> list[Event]:
    """Poll the DB for this call's events and print them until the call finishes."""
    events: list[Event] = []
    shown_manual_hint = False
    while True:
        done = run.task is not None and run.task.done()
        for e in await history(run.call_id, since=events[-1]["seq"] if events else 0):
            events.append(e)
            line = format_event(e, run.raw_token)
            if line:
                print(line, flush=True)
            if run.mode == "manual" and e["type"] == EventType.confirm_requested and not shown_manual_hint:
                shown_manual_hint = True
                print(
                    c(BOLD, f"\n>>> Manual mode: tap Confirm within 180s, e.g.\n    curl -X POST "
                      f"localhost:8000/api/s/{run.raw_token}/confirm   (needs `make backend` running)\n"),
                    flush=True,
                )
        if done:
            return events
        await asyncio.sleep(0.1)


async def end_state_check(run: CallRun, events: list[Event]) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    def first_seq(etype: str, **match: Any) -> int | None:
        return next(
            (e["seq"] for e in events if e["type"] == etype and all(e["payload"].get(k) == v for k, v in match.items())),
            None,
        )

    check("call finished without error", run.error is None, run.error or "")

    async with SessionLocal() as session:
        if run.scenario.name == "order":
            order = await session.scalar(select(Order).where(Order.call_id == run.call_id))
            check("order placed", bool(order and order.status == OrderStatus.placed),
                  f"status={order.status.value if order else None}")
            cart = next((e["payload"] for e in reversed(events) if e["type"] == EventType.state_cart), {"items": []})
            got = {i["sku"]: i["qty"] for i in cart["items"]}
            check("cart is exactly 2 × ribeye + 1 × compound butter", got == {RIBEYE: 2, BUTTER: 1}, json.dumps(got))
            c3_turn = next(
                (e["payload"]["turn_id"] for e in events
                 if e["type"] == EventType.transcript_turn and e["payload"].get("beat_id") == "c3"),
                None,
            )
            check("transcript.correction exists for c3",
                  c3_turn is not None and first_seq(EventType.transcript_correction, turn_id=c3_turn) is not None,
                  f"c3 turn_id={c3_turn}")
        else:
            r = await customer_reservation(session, run.scenario.customer_id)
            got = (r.status.value, r.party_size, r.date.isoformat(), r.time.strftime("%H:%M")) if r else None
            check("reservation confirmed, party 5, 2026-12-04 19:30",
                  got == ("confirmed", 5, "2026-12-04", "19:30"), str(got))

        token = await session.scalar(select(LinkToken).where(LinkToken.call_id == run.call_id))

    connected = first_seq(EventType.call_status, status="connected")
    minted = first_seq(EventType.link_minted)
    check("link minted right after connected", bool(connected and minted and minted == connected + 1),
          f"connected seq={connected}, minted seq={minted}")
    yes = first_seq(EventType.transcript_turn, beat_id=YES_BEAT)
    sent = first_seq(EventType.link_sent)
    check(f"link sent after the customer said yes ({YES_BEAT})", bool(yes and sent and sent > yes),
          f"yes seq={yes}, sent seq={sent}")
    check("token revoked (DB + link.revoked event)",
          bool(token and token.revoked_at and first_seq(EventType.link_revoked)), "")
    if run.raw_token:
        from app.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://link.local") as http:
            status = (await http.get(f"/api/s/{run.raw_token}")).status_code
        check("GET /api/s/{token} returns 410 after the call", status == 410, f"got {status}")
    else:
        check("GET /api/s/{token} returns 410 after the call", False, "no token was minted")
    return checks


async def main(scenario: str, manual: bool, speed: float) -> int:
    logging.basicConfig(level=logging.WARNING, format=f"{PAD}%(levelname)s %(name)s: %(message)s")
    logging.getLogger("app.call.agent").setLevel(logging.INFO)  # unspoken between-tool notes
    await init_db()

    started_at = dt.datetime.now(dt.UTC)
    t0 = time.monotonic()
    run = await start_call(scenario, "manual" if manual else "auto", speed)
    print(c(BOLD, f"{scenario} · call {run.call_id} · {run.mode} · speed {speed}"), flush=True)
    events = await print_live(run)
    await run.task  # already done; re-raises nothing (run() catches)
    duration = time.monotonic() - t0

    checks = await end_state_check(run, events)
    passed = all(ok for _, ok, _ in checks)

    print(c(BOLD, "\nEND-STATE CHECK"))
    for label, ok, detail in checks:
        mark = c(GREEN, "PASS") if ok else c(RED, "FAIL")
        print(f"  {mark}  {label}" + (c(DIM, f"  ({detail})") if detail and not ok else ""))

    agent = run.agent
    usage = agent.usage.as_dict(agent.model) if agent else None
    print()
    print(c(BOLD, "RESULT: ") + (c(GREEN + ";" + BOLD, "PASS") if passed else c(RED + ";" + BOLD, "FAIL")))
    print(f"duration: {duration:.1f}s · silence nudges: {len(run.nudges)}" + (f" (before {', '.join(run.nudges)})" if run.nudges else ""))
    if usage:
        cost = f"≈ ${usage['cost_usd']:.4f}" if usage["cost_usd"] is not None else "cost n/a for this model"
        print(
            f"LLM ({usage['model']}): {usage['requests']} requests · input {usage['input_tokens']:,} uncached"
            f" + {usage['cache_read_input_tokens']:,} cache read + {usage['cache_creation_input_tokens']:,} cache write"
            f" · output {usage['output_tokens']:,} · {cost}"
        )
        if agent.unspoken:
            print(c(DIM, f"{len(agent.unspoken)} between-tool note(s) came back as thinking blocks (not spoken)"))

    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{scenario}-{started_at:%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "scenario": scenario,
                "call_id": str(run.call_id),
                "mode": run.mode,
                "speed": speed,
                "started_at": started_at.isoformat(),
                "duration_s": round(duration, 2),
                "passed": passed,
                "error": run.error,
                "nudges": run.nudges,
                "checks": [{"label": l, "ok": ok, "detail": d} for l, ok, d in checks],
                "usage": usage,
                "unspoken_notes": agent.unspoken if agent else [],
                "events": events,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"events: {path.relative_to(RUNS_DIR.parent)}")
    await engine.dispose()
    return 0 if passed else 1


def cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.call.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario", choices=["order", "reservation"])
    parser.add_argument("--manual", action="store_true", help="wait for a real Confirm click (up to 180s)")
    parser.add_argument("--speed", type=float, default=1.0, help="pacing multiplier (4 = four times faster)")
    a = parser.parse_args()
    if a.speed <= 0:
        parser.error("--speed must be > 0")
    sys.exit(asyncio.run(main(a.scenario, a.manual, a.speed)))


if __name__ == "__main__":
    cli()
