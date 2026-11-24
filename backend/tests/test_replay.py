"""Replay engine: gap capping, a fresh real token, the confirm pause. No LLM or TTS calls."""

import asyncio
import datetime as dt
import json

import pytest

from app.call import replay as replay_mod
from app.events import EventType, history
from app.links import live_url

T0 = dt.datetime(2026, 11, 24, 10, 0, 0, tzinfo=dt.UTC)


def ev(seq, ms, etype, **payload):
    return {"seq": seq, "type": etype, "payload": payload, "created_at": (T0 + dt.timedelta(milliseconds=ms)).isoformat()}


def test_schedule_keeps_speech_and_ringing_but_caps_silence():
    events = [
        ev(1, 0, "call.status", status="ringing"),
        ev(2, 2000, "call.status", status="connected"),  # 2s ringing: kept
        ev(3, 2000, "link.minted"),
        ev(4, 5000, "transcript.turn", speaker="agent", text_said="Hi"),  # 3s LLM silence -> 0.7s
        ev(5, 5000, "audio.play", duration_ms=4000),
        ev(6, 11000, "transcript.turn", speaker="customer", text_said="Hello"),  # 4s speech kept + 2s silence -> 0.7s
    ]
    offsets = replay_mod.schedule(events, max_gap_ms=700)
    assert offsets == [0, 2000, 2000, 2700, 2700, 2700 + 4000 + 700]


def _synthetic_run():
    return {
        "scenario": "order",
        "passed": True,
        "tts": True,
        "started_at": T0.isoformat(),
        "duration_s": 9.0,
        "audio": {},
        "events": [
            ev(1, 0, "call.status", status="dialing"),
            ev(2, 50, "call.status", status="ringing"),
            ev(3, 100, "call.status", status="connected"),
            ev(4, 100, "link.minted", token_id=1, expires_at="x"),
            ev(5, 3000, "transcript.turn", turn_id=1, speaker="customer", name="Jamie", text_said="Sure, text it to me.",
               text_heard="Sure, text it to me.", beat_id="c3", marks="link_accepted"),
            ev(6, 3100, "link.sent", to="+1 •••-•••-0142", sms_text="old token text"),
            ev(7, 3100, "link.opened", token_id=1),
            ev(8, 3200, "state.cart", items=[{"sku": "FP-RIBEYE-DA16", "qty": 2}], subtotal_cents=14400),
            ev(9, 3300, "confirm.requested", summary="2 x ribeye"),
            ev(10, 3400, "transcript.turn", turn_id=2, speaker="customer", name="Jamie",
               text_said="Okay, confirming now.", text_heard="Okay, confirming now.", beat_id="c7", action="confirm"),
            ev(11, 3500, "confirm.received", via="link"),
            ev(12, 9000, "call.status", status="ended", reason="agent_hung_up"),
            ev(13, 9000, "link.revoked", token_id=1),
        ],
    }


@pytest.fixture
def run_dirs(tmp_path, monkeypatch):
    takes, runs = tmp_path / "takes", tmp_path / "runs"
    takes.mkdir()
    runs.mkdir()
    monkeypatch.setattr(replay_mod, "TAKES_DIR", takes)
    monkeypatch.setattr(replay_mod, "RUNS_DIR", runs)
    (runs / "order-20261124-100000.json").write_text(json.dumps(_synthetic_run()))
    return takes, runs


async def test_replay_is_a_new_call_with_a_real_token_and_waits_for_a_real_click(run_dirs, http):
    takes = replay_mod.list_takes()
    assert [t["run"] for t in takes] == ["order-20261124-100000.json"]
    run = await replay_mod.start_replay("order-20261124-100000.json", max_gap_ms=50)

    # It pauses at the recorded confirm.received point until someone taps Confirm.
    for _ in range(100):
        if run.waiting_for_confirm:
            break
        await asyncio.sleep(0.05)
    assert run.waiting_for_confirm
    types = [e["type"] for e in await history(run.call_id)]
    assert "confirm.requested" in types and "confirm.received" not in types and "link.opened" not in types

    token = live_url(run.call_id).rsplit("/", 1)[1]
    assert (await http.get(f"/api/s/{token}")).json()["cart"]["items"][0]["qty"] == 2  # snapshot from events
    assert (await http.post(f"/api/s/{token}/confirm")).status_code == 200
    await asyncio.wait_for(run.task, 5)

    events = await history(run.call_id)
    types = [e["type"] for e in events]
    assert types.count("confirm.received") == 1 and types.count("link.minted") == 1
    assert types[-2:] == ["call.status", "link.revoked"]
    sent = next(e for e in events if e["type"] == "link.sent")["payload"]
    assert sent["sms_text"] == f"Private link: …/s/{token[:4]}•••• (expires in 30 min)"  # no host, port or token
    assert sent["from"] == "Fieldstone Provisions" and sent["to"] == "+1 •••-•••-0142"
    assert (await http.get(f"/api/s/{token}")).status_code == 410
    assert run.error is None


async def test_star_copies_a_run_into_takes(run_dirs):
    takes, _ = run_dirs
    replay_mod.star("order-20261124-100000.json")
    assert (takes / "order-20261124-100000.json").is_file()
    assert replay_mod.list_takes()[0]["starred"] is True
    with pytest.raises(replay_mod.ReplayError):
        replay_mod.run_path("../etc/passwd")


def test_event_type_list_is_complete():
    assert EventType.audio_play.value == "audio.play"
