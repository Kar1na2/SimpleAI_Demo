"""Agent tool loop with a scripted fake model (no LLM calls)."""

from types import SimpleNamespace

import pytest

from app.call import agent as agent_mod
from app.config import Settings


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, args, i=[0]):  # noqa: B006 - a counter for unique tool_use ids
    i[0] += 1
    return SimpleNamespace(type="tool_use", name=name, input=args, id=f"toolu_{i[0]}")


def response(blocks, stop="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None)


@pytest.fixture
def make_agent(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_settings", lambda: Settings(anthropic_api_key="test-key"))

    def make(ctx, scripted):
        said: list[str] = []

        async def on_say(t):
            said.append(t)

        a = agent_mod.Agent(ctx.scenario, ctx, on_say)
        requests = []

        async def fake_create():
            requests.append(1)
            return scripted.pop(0)

        a._create = fake_create
        return a, said, requests

    return make


async def test_silent_end_call_gets_exactly_one_goodbye_request(order_call, make_agent):
    a, said, requests = make_agent(
        order_call,
        [
            response([tool("add_note", {"text": "Order placed."}), tool("end_call", {})]),
            response([text("You're all set, Jamie. Goodbye!")], "end_turn"),
        ],
    )
    await a.respond(["[System: the customer tapped Confirm on the link.]"])
    assert said == ["You're all set, Jamie. Goodbye!"]
    assert len(requests) == 2 and order_call.end_requested
    end_result = a.messages[2]["content"][1]
    assert "goodbye" in end_result["content"].lower()


async def test_spoken_goodbye_then_end_call_hangs_up_at_once(order_call, make_agent):
    a, said, requests = make_agent(
        order_call, [response([text("Thanks Jamie, goodbye!"), tool("end_call", {})])]
    )
    await a.respond(["Great, thanks Ava. Bye!"])
    assert said == ["Thanks Jamie, goodbye!"] and len(requests) == 1


async def test_goodbye_request_happens_at_most_once(order_call, make_agent):
    # The extra request comes back silent again: hang up anyway, don't loop.
    a, said, requests = make_agent(
        order_call,
        [response([tool("end_call", {})]), response([tool("end_call", {})])],
    )
    await a.respond(["[System: the customer tapped Confirm on the link.]"])
    assert said == [] and len(requests) == 2
