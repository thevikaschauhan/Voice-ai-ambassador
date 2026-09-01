"""Hook 3: post-turn brief extraction, and its failure path.

Two properties matter and both are asserted against behaviour rather than
internals: the extraction never blocks the voice path, and invalid output gets
exactly one repair attempt before the last good brief is kept (docs/03-).

Validator 3 is in here too: a `shortlist_ids` entry that does not resolve to an
inventory record is a guardrail failure, not something to silently drop.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from adapter.brief import BriefExtractor
from ambassador.schemas import LeadBrief

FAKE_KEY = "test-key-not-a-real-credential"
PROJECT_IDS = ["binghatti-skyrise", "binghatti-aquarise"]

VALID_BRIEF = {
    "intent": "invest",
    "budget": {"amount": 1000000, "currency": "AED", "confirmed": True},
    "unit_preference": "studio",
    "timeline": "6 months",
    "buyer_location": "Mumbai",
    "golden_visa_interest": True,
    "hesitations": ["handover date"],
    "shortlist_ids": ["binghatti-skyrise"],
    "stage": "discovery",
    "language": "en",
}

TRANSCRIPT = [
    {"role": "user", "content": "What does a studio at Skyrise cost?"},
    {"role": "assistant", "content": "It starts at 985,000 dirhams."},
]


class ScriptedTransport(httpx.AsyncBaseTransport):
    """Returns a queued response per call, recording what was asked."""

    def __init__(self, contents: list[str], *, delay: float = 0.0) -> None:
        self._contents = list(contents)
        self._delay = delay
        self.requests: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(json.loads(request.content))
        if self._delay:
            await asyncio.sleep(self._delay)
        content = self._contents.pop(0) if self._contents else "{}"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )


class PacedTransport(httpx.AsyncBaseTransport):
    """A queue of (delay, content) pairs, so two concurrent extractions can be
    made to complete in a chosen order."""

    def __init__(self, scripted: list[tuple[float, str]]) -> None:
        self._scripted = list(scripted)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        delay, content = self._scripted.pop(0)
        if delay:
            await asyncio.sleep(delay)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 0}},
            },
        )


def make_extractor(
    transport: httpx.AsyncBaseTransport, events: list[tuple]
) -> BriefExtractor:
    return BriefExtractor(
        api_key=FAKE_KEY,
        model="qwen/qwen3.7-flash",
        base_url="https://openrouter.ai/api/v1",
        project_ids=PROJECT_IDS,
        language="en",
        on_event=lambda name, **fields: events.append((name, fields)),
        client=httpx.AsyncClient(transport=transport),
    )


# --- the happy path -------------------------------------------------------


async def test_valid_json_becomes_a_validated_lead_brief():
    events: list[tuple] = []
    extractor = make_extractor(ScriptedTransport([json.dumps(VALID_BRIEF)]), events)

    await extractor.schedule(TRANSCRIPT, turn_index=1)

    assert isinstance(extractor.last_good, LeadBrief)
    assert extractor.last_good.budget.currency == "AED"
    assert extractor.last_good.shortlist_ids == ["binghatti-skyrise"]
    names = [n for n, _ in events]
    assert names == ["brief"]


async def test_thinking_is_disabled_on_the_extraction_call_too():
    events: list[tuple] = []
    transport = ScriptedTransport([json.dumps(VALID_BRIEF)])
    await make_extractor(transport, events).schedule(TRANSCRIPT, turn_index=1)

    body = transport.requests[0]
    assert body["reasoning"] == {"enabled": False}
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}


async def test_code_fenced_json_is_still_accepted():
    events: list[tuple] = []
    fenced = "```json\n" + json.dumps(VALID_BRIEF) + "\n```"
    extractor = make_extractor(ScriptedTransport([fenced]), events)

    await extractor.schedule(TRANSCRIPT, turn_index=1)

    assert extractor.last_good is not None
    assert extractor.last_good.intent == "invest"


# --- the failure path (docs/03-) ------------------------------------------


async def test_invalid_output_is_repaired_on_exactly_one_retry():
    events: list[tuple] = []
    transport = ScriptedTransport(["not json at all", json.dumps(VALID_BRIEF)])
    extractor = make_extractor(transport, events)

    await extractor.schedule(TRANSCRIPT, turn_index=1)

    assert len(transport.requests) == 2
    assert extractor.last_good is not None
    names = [n for n, _ in events]
    assert names == ["brief_invalid", "brief"]
    # The repair prompt names the failure rather than just asking again.
    repair_text = transport.requests[1]["messages"][-1]["content"]
    assert "rejected" in repair_text


async def test_two_failures_keep_the_last_good_brief():
    events: list[tuple] = []
    good = ScriptedTransport([json.dumps(VALID_BRIEF)])
    extractor = make_extractor(good, events)
    await extractor.schedule(TRANSCRIPT, turn_index=1)
    first_good = extractor.last_good
    assert first_good is not None

    # Same extractor, a later turn that fails twice.
    extractor._client = httpx.AsyncClient(
        transport=ScriptedTransport(["{", "also bad"])
    )
    await extractor.schedule(TRANSCRIPT, turn_index=2)

    assert extractor.last_good == first_good  # unchanged, not cleared
    names = [n for n, _ in events]
    assert names[-1] == "brief_fallback"
    assert events[-1][1]["kept_last_good"] is True


async def test_unresolvable_shortlist_id_is_a_failure_not_a_silent_drop():
    """docs/03- validator 3. Dropping the bad id would hide exactly the failure
    mode this system claims to prevent."""
    events: list[tuple] = []
    bad = {**VALID_BRIEF, "shortlist_ids": ["binghatti-marina-heights"]}
    transport = ScriptedTransport([json.dumps(bad), json.dumps(VALID_BRIEF)])
    extractor = make_extractor(transport, events)

    await extractor.schedule(TRANSCRIPT, turn_index=1)

    first_event, first_fields = events[0]
    assert first_event == "brief_invalid"
    assert "binghatti-marina-heights" in first_fields["error"]
    # Recovered on the retry, and the bad id is nowhere in the kept brief.
    assert extractor.last_good.shortlist_ids == ["binghatti-skyrise"]


# --- off the latency path -------------------------------------------------


# --- out-of-order completion ----------------------------------------------


async def test_a_late_turn_does_not_overwrite_a_newer_brief():
    """Extraction is detached and retries, so turn N can finish after turn N+1.
    The last good brief only ever moves forward."""
    events: list[tuple] = []
    newer = {**VALID_BRIEF, "stage": "booking"}
    older = {**VALID_BRIEF, "stage": "opening"}

    extractor = make_extractor(ScriptedTransport([json.dumps(newer)]), events)
    await extractor.schedule(TRANSCRIPT, turn_index=2)
    assert extractor.last_good.stage == "booking"

    # Turn 1's slow extraction lands afterwards.
    extractor._client = httpx.AsyncClient(
        transport=ScriptedTransport([json.dumps(older)])
    )
    await extractor.schedule(TRANSCRIPT, turn_index=1)

    assert extractor.last_good.stage == "booking"
    assert extractor.last_accepted_turn == 2
    names = [n for n, _ in events]
    assert names == ["brief", "brief_stale_dropped"]
    assert events[-1][1]["turn"] == 1
    assert events[-1][1]["last_accepted_turn"] == 2


async def test_overlapping_extractions_completing_out_of_order_keep_the_newer():
    """The real shape of the race: both extractions in flight at once, the
    older turn's the slower of the two."""
    events: list[tuple] = []
    newer = {**VALID_BRIEF, "stage": "booking"}
    older = {**VALID_BRIEF, "stage": "opening"}

    extractor = BriefExtractor(
        api_key=FAKE_KEY,
        model="qwen/qwen3.7-flash",
        base_url="https://openrouter.ai/api/v1",
        project_ids=PROJECT_IDS,
        language="en",
        on_event=lambda name, **fields: events.append((name, fields)),
        client=httpx.AsyncClient(
            # Call order is task creation order; turn 1 goes first and is slow.
            transport=PacedTransport(
                [(0.2, json.dumps(older)), (0.0, json.dumps(newer))]
            )
        ),
    )

    task_old = extractor.schedule(TRANSCRIPT, turn_index=1)
    await asyncio.sleep(0)  # let turn 1 issue its request before turn 2 exists
    task_new = extractor.schedule(TRANSCRIPT, turn_index=2)

    await task_new
    assert extractor.last_good.stage == "booking"  # the newer one landed first
    await task_old

    assert extractor.last_good.stage == "booking"  # and the older one did not win
    assert extractor.last_accepted_turn == 2
    assert [n for n, _ in events] == ["brief", "brief_stale_dropped"]


async def test_a_repeat_of_the_same_turn_is_still_accepted():
    """The guard drops older turns, not equal ones: a repair retry on the same
    turn must still be able to land."""
    events: list[tuple] = []
    extractor = make_extractor(ScriptedTransport([json.dumps(VALID_BRIEF)]), events)
    await extractor.schedule(TRANSCRIPT, turn_index=3)

    later = {**VALID_BRIEF, "stage": "booking"}
    extractor._client = httpx.AsyncClient(
        transport=ScriptedTransport([json.dumps(later)])
    )
    await extractor.schedule(TRANSCRIPT, turn_index=3)

    assert extractor.last_good.stage == "booking"
    assert "brief_stale_dropped" not in [n for n, _ in events]


async def test_scheduling_returns_immediately_and_does_not_block_the_caller():
    """The whole point of the third channel: the voice path must not wait."""
    events: list[tuple] = []
    slow = ScriptedTransport([json.dumps(VALID_BRIEF)], delay=0.25)
    extractor = make_extractor(slow, events)

    loop = asyncio.get_running_loop()
    started = loop.time()
    task = extractor.schedule(TRANSCRIPT, turn_index=1)
    scheduling_cost = loop.time() - started

    assert scheduling_cost < 0.02  # returned without awaiting the request
    assert not task.done()

    await extractor.drain()
    assert extractor.last_good is not None
