"""Run one scenario end to end in this process, print the live transcript, check the end state.

    uv run python -m app.call.run order [--speed 4]
    uv run python -m app.call.run order --tts             # real OpenAI voices, real clip lengths
    uv run python -m app.call.run reservation --manual    # waits up to 180s for a real Confirm click

Auto mode needs only Postgres and ANTHROPIC_API_KEY (--tts also needs OPENAI_API_KEY). Manual
mode also needs `make backend` running, so the Confirm click has an endpoint to hit. This process
polls the DB for the click.

The call itself writes backend/runs/<scenario>-<timestamp>.json (app/call/checks.py).
Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from app.call.checks import RUNS_DIR
from app.call.orchestrator import CallRun, start_call
from app.call.state import money
from app.db import engine, init_db
from app.events import Event, EventType, history

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
    if t in (EventType.note_added, EventType.audio_play):
        return None  # shown by its add_note tool call / its transcript.turn
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


async def main(scenario: str, manual: bool, speed: float, tts: bool) -> int:
    logging.basicConfig(level=logging.WARNING, format=f"{PAD}%(levelname)s %(name)s: %(message)s")
    logging.getLogger("app.call.agent").setLevel(logging.INFO)  # unspoken between-tool notes
    await init_db()

    run = await start_call(scenario, "manual" if manual else "auto", speed, tts=tts)
    voice = " · TTS" if tts else ""
    print(c(BOLD, f"{scenario} · call {run.call_id} · {run.mode} · speed {speed}{voice}"), flush=True)
    await print_live(run)
    await run.task  # already done; run() catches everything and records the run file

    print(c(BOLD, "\nEND-STATE CHECK"))
    for label, ok, detail in run.checks:
        mark = c(GREEN, "PASS") if ok else c(RED, "FAIL")
        print(f"  {mark}  {label}" + (c(DIM, f"  ({detail})") if detail and not ok else ""))

    agent = run.agent
    usage = agent.usage.as_dict(agent.model) if agent else None
    print()
    print(c(BOLD, "RESULT: ") + (c(GREEN + ";" + BOLD, "PASS") if run.passed else c(RED + ";" + BOLD, "FAIL")))
    nudges = f" (before {', '.join(run.nudges)})" if run.nudges else ""
    print(f"duration: {run.duration or 0:.1f}s · silence nudges: {len(run.nudges)}{nudges}")
    if usage:
        cost = f"≈ ${usage['cost_usd']:.4f}" if usage["cost_usd"] is not None else "cost n/a for this model"
        print(
            f"LLM ({usage['model']}): {usage['requests']} requests · input {usage['input_tokens']:,} uncached"
            f" + {usage['cache_read_input_tokens']:,} cache read + {usage['cache_creation_input_tokens']:,} cache write"
            f" · output {usage['output_tokens']:,} · {cost}"
        )
        if agent.unspoken:
            print(c(DIM, f"{len(agent.unspoken)} between-tool note(s) came back as thinking blocks (not spoken)"))
    if run.audio:
        total = sum(a["duration_ms"] for a in run.audio.values()) / 1000
        print(f"TTS: {len(run.audio)} clips, {total:.1f}s of audio")
    print(f"run file: {RUNS_DIR.name}/{run.run_file}" if run.run_file else c(RED, "run file: not written"))
    await engine.dispose()
    return 0 if run.passed else 1


def cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.call.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario", choices=["order", "reservation"])
    parser.add_argument("--manual", action="store_true", help="wait for a real Confirm click (up to 180s)")
    parser.add_argument("--speed", type=float, default=1.0, help="pacing multiplier (4 = four times faster)")
    parser.add_argument("--tts", action="store_true", help="OpenAI TTS for every turn; pacing = real clip length")
    a = parser.parse_args()
    if a.speed <= 0:
        parser.error("--speed must be > 0")
    sys.exit(asyncio.run(main(a.scenario, a.manual, a.speed, a.tts)))


if __name__ == "__main__":
    cli()
