"""Beat wait_for evaluation (pure, no DB) and the scenario files."""

import pytest

from app.call.scenario import BeatView, CallView, ScenarioError, StatePredicate, WaitFor, load_scenario


def ev(seq, etype, **payload):
    return {"seq": seq, "type": etype, "payload": payload}


def cart(**qty):
    return {"items": [{"sku": sku, "qty": q} for sku, q in qty.items()]}


def test_agent_said_matches_is_case_insensitive_regex():
    w = WaitFor.parse({"agent_said_matches": "7:30|seven[- ]thirty|19:30"}, "c4")
    assert w.unmet(BeatView(agent_text="I have Seven-Thirty or six thirty.")) == []
    assert w.unmet(BeatView(agent_text="We have 7:30 PM open.")) == []
    assert w.unmet(BeatView(agent_text="Only six thirty, sorry.")) == ["agent_said_matches /7:30|seven[- ]thirty|19:30/"]


def test_event_must_have_occurred_since_previous_beat():
    w = WaitFor.parse({"event": "link.sent"}, "c3")
    view = CallView()
    view.apply(ev(1, "link.sent", to="x"))
    assert w.unmet(view.beat) == []
    # A customer beat resets the window.
    view.apply(ev(2, "transcript.turn", speaker="customer", text_said="hi"))
    assert w.unmet(view.beat) == ["event link.sent since the previous beat"]


def test_cart_predicate_needs_the_item_present():
    p = StatePredicate.parse("cart.FP-RIBEYE-DA16.qty != 2")
    assert not p.holds(None, None)  # nothing in the cart yet: the customer can't see a quantity
    assert not p.holds(cart(**{"FP-BUTTER-GH": 1}), None)
    assert p.holds(cart(**{"FP-RIBEYE-DA16": 10}), None)
    assert not p.holds(cart(**{"FP-RIBEYE-DA16": 2}), None)


def test_reservation_predicate():
    p = StatePredicate.parse("reservation.party_size == 5")
    assert not p.holds(None, None)
    assert not p.holds(None, {"party_size": 4})
    assert p.holds(None, {"party_size": 5})
    q = StatePredicate.parse('reservation.time == "19:30"')
    assert q.holds(None, {"time": "19:30"})


def test_all_conditions_must_hold():
    w = WaitFor.parse({"agent_said_matches": "butter", "event": "state.cart"}, "x")
    view = CallView()
    view.apply(ev(1, "transcript.turn", speaker="agent", text_said="Want some garlic-herb butter?"))
    assert w.unmet(view.beat) == ["event state.cart since the previous beat"]
    view.apply(ev(2, "state.cart", items=[]))
    assert w.unmet(view.beat) == []


def test_agent_text_accumulates_across_turns_until_customer_speaks():
    view = CallView()
    view.apply(ev(1, "transcript.turn", speaker="agent", text_said="Sent!"))
    view.apply(ev(2, "transcript.turn", speaker="agent", text_said="Check the link."))
    assert view.beat.agent_text == "Sent! Check the link."
    view.apply(ev(3, "transcript.turn", speaker="customer", text_said="ok"))
    assert view.beat.agent_text == ""
    view.apply(ev(2, "state.cart", items=[]))  # stale seq is ignored
    assert view.beat.cart is None


@pytest.mark.parametrize(
    "raw",
    ["cart.FP-RIBEYE-DA16.qty ~ 2", "cart.X.qty != two", "basket.x == 1", "reservation.party_size =="],
)
def test_bad_state_predicates_are_rejected(raw):
    with pytest.raises(ScenarioError):
        StatePredicate.parse(raw)


def test_unknown_event_type_is_rejected():
    with pytest.raises(ScenarioError):
        WaitFor.parse({"event": "link.sendt"}, "c3")


def test_order_scenario_matches_the_brief():
    s = load_scenario("order")
    assert (s.brand, s.agent_name, s.direction, s.customer_id) == ("Fieldstone Provisions", "Ava", "outbound", 1)
    assert [b.id for b in s.beats] == ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]
    c2, c3, c4, c5, c7, c8 = (s.beat(b) for b in ("c2", "c3", "c4", "c5", "c7", "c8"))
    assert c2.wait_for.agent_said_matches.pattern == r"\?" and c2.wait_for.agent_not_yet_said.pattern == "link"
    assert c3.marks == "link_accepted"
    assert c4.say == "Got it, it's open. So, I'd like two of the ribeyes."
    assert c4.heard == "Got it, it's open. So, I'd like ten of the ribeyes."
    assert c5.corrects == "c4" and [p.raw for p in c5.wait_for.state] == ["cart.FP-RIBEYE-DA16.qty != 2"]
    assert c7.action == "confirm" and c7.wait_for.event == "confirm.requested"
    assert c8.optional and c8.say == "Great, thanks Ava. Bye!"
    assert "Before we continue, I can text you a link" in s.system_prompt


def test_reservation_scenario_matches_the_brief():
    s = load_scenario("reservation")
    assert (s.brand, s.agent_name, s.direction, s.customer_id) == ("Juniper Table", "Leo", "inbound", 2)
    assert [b.id for b in s.beats] == ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    assert [p.raw for p in s.beat("c3").wait_for.state] == ["reservation.party_size == 5"]
    assert s.beat("c4").wait_for.agent_said_matches.pattern == "7:30|seven[- ]thirty|19:30"
    c5 = s.beat("c5")
    assert c5.say == "No, that's everything." and c5.wait_for.agent_said_matches.pattern == "anything else"
    assert [p.raw for p in c5.wait_for.state] == ["reservation.date == 2026-12-04", "reservation.time == 19:30"]
    assert s.beat("c6").action == "confirm" and s.beat("c7").optional


def test_agent_not_yet_said_looks_at_the_whole_call():
    w = WaitFor.parse({"agent_said_matches": r"\?", "agent_not_yet_said": "link"}, "c2")
    view = CallView()
    view.apply(ev(1, "transcript.turn", speaker="agent", text_said="Hi Jamie, this is Ava."))
    view.apply(ev(2, "transcript.turn", speaker="customer", text_said="Oh, hi!"))
    view.apply(ev(3, "transcript.turn", speaker="agent", text_said="What are you shopping for?"))
    assert w.unmet(view.beat) == []
    view.apply(ev(4, "transcript.turn", speaker="agent", text_said="I can text you a link."))
    assert w.unmet(view.beat) == ["agent_not_yet_said /link/ (the agent already said it)"]
    view.apply(ev(5, "transcript.turn", speaker="customer", text_said="ok"))
    view.apply(ev(6, "transcript.turn", speaker="agent", text_said="Anything else?"))
    assert w.unmet(view.beat), "a link mention earlier in the call still counts"


def test_state_list_needs_every_predicate():
    w = WaitFor.parse({"state": ["reservation.date == 2026-12-04", "reservation.time == 19:30"]}, "c5")
    assert w.unmet(BeatView(reservation={"date": "2026-12-04", "time": "19:30"})) == []
    assert w.unmet(BeatView(reservation={"date": "2026-12-04", "time": "19:00"})) == ["state reservation.time == 19:30"]
