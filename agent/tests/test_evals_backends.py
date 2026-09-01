"""The two model backends, and the round trip that makes an escalation audible.

The live backend is exercised against a fake transport rather than the network:
what needs pinning is the REQUEST shape and the tool-result round trip, and
both are checkable without spending anything. The recording runs that produced
`source: recorded` fixtures are the only thing that talks to OpenRouter, and
they are deliberate and counted.
"""

from __future__ import annotations

import json

import httpx
import pytest

from evals.backends import (
    ESCALATE_TOOL,
    TOOL_RESULTS,
    TOOL_SCHEMAS,
    BackendError,
    FixtureBackend,
    LiveBackend,
    ModelRequest,
)
from evals.cases import EvalCase, ModelFixture


def case() -> EvalCase:
    return EvalCase.model_validate(
        {
            "id": "t",
            "category": "grounding_happy_path",
            "language": "en",
            "turns": [{"buyer": "hello"}],
            "assertions": [{"kind": "must_not_escalate"}],
        }
    )


def request(fixture=None, *, detail=None) -> ModelRequest:
    return ModelRequest(
        case_id="t",
        language="en",
        system_prompt="SYSTEM",
        messages=(("user", "hello"),),
        fixture=fixture,
        regeneration_detail=detail,
    )


def fixture(**kwargs) -> ModelFixture:
    return ModelFixture.model_validate(
        {
            "source": "authored",
            "intent": "compliant",
            "note": "n",
            "text": "t",
            **kwargs,
        }
    )


def reply(*, content=None, tool_calls=None):
    message: dict = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def live(handler, **kwargs) -> LiveBackend:
    return LiveBackend(
        api_key="test-key",
        model="test/model",
        base_url="https://example.invalid/api/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


# --- offline ---------------------------------------------------------------


def test_a_missing_fixture_fails_rather_than_disappearing():
    """A case that cannot run must fail. A pass rate that quietly excludes the
    cases nobody could answer is worse than no number."""
    with pytest.raises(BackendError, match="no model fixture"):
        FixtureBackend().reply(request(None))


def test_a_blocked_reply_with_no_authored_retry_fails_loudly():
    """The live path always gets one regeneration, so a fixture that authors
    none is incomplete - and silently skipping the retry would measure a
    recovery the product does not have."""
    with pytest.raises(BackendError, match="authors no `retry`"):
        FixtureBackend().reply(request(fixture(), detail="blocked: 750,000"))


def test_the_retry_fixture_is_what_is_replayed_on_a_regeneration():
    outer = fixture(
        text="AED 750,000.",
        retry={"source": "recorded", "intent": "compliant", "note": "n", "text": "no."},
    )
    backend = FixtureBackend()
    assert backend.reply(request(outer)).text == "AED 750,000."
    retried = backend.reply(request(outer, detail="blocked"))
    assert retried.text == "no."
    assert retried.source == "recorded"


# --- live: the request shape -----------------------------------------------


def test_live_mode_needs_a_key_and_spends_nothing_without_one():
    with pytest.raises(BackendError, match="OPENROUTER_API_KEY"):
        LiveBackend(api_key="", model="m", base_url="https://example.invalid")


def test_the_request_carries_the_prompt_the_tools_and_thinking_off():
    """ADR-016's trap: reasoning is on by default for this model and thinking
    tokens run before any reply. temperature 0 because the runbook wants three
    consecutive identical runs."""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json=reply(content="fine"))

    live(handler).reply(request(fixture()))
    assert seen["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert seen["messages"][1] == {"role": "user", "content": "hello"}
    assert seen["temperature"] == 0.0
    assert seen["reasoning"] == {"enabled": False}
    assert seen["auth"] == "Bearer test-key"
    assert {t["function"]["name"] for t in seen["tools"]} == {
        t["function"]["name"] for t in TOOL_SCHEMAS
    }


def test_thinking_can_be_left_on_explicitly():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=reply(content="fine"))

    live(handler, thinking_disabled=False).reply(request(fixture()))
    assert "reasoning" not in seen


def test_the_regeneration_instruction_is_appended_as_a_system_message():
    """Mirrors adapter/agent.py, which copies the chat context and appends the
    instruction as a system message before reopening the stream."""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=reply(content="fine"))

    live(handler).reply(request(fixture(), detail="blocked: 750,000"))
    assert seen["messages"][-1] == {
        "role": "system",
        "content": "blocked: 750,000",
    }


# --- live: the tool-result round trip -------------------------------------


def test_a_tool_call_with_no_content_gets_the_second_inference():
    """The framework hands a tool's return value back and runs another turn,
    and THAT is the turn the buyer hears. Measured live on an Arabic "I want to
    speak to a real person": the first inference returned escalate_to_human with
    empty content, so stopping there records an escalation as silence - and
    escalation is five of the ten gated categories."""
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json=reply(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "function": {"name": ESCALATE_TOOL, "arguments": "{}"},
                        }
                    ],
                ),
            )
        return httpx.Response(200, json=reply(content="A colleague will call you."))

    backend = live(handler)
    result = backend.reply(request(fixture()))

    assert result.text == "A colleague will call you."
    assert result.tools == (ESCALATE_TOOL,)
    assert backend.calls == 2

    # The tool's own return value is what the model is answering.
    tool_message = bodies[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "c1"
    assert tool_message["content"] == TOOL_RESULTS[ESCALATE_TOOL]
    # And the assistant turn that made the call is still in the context.
    assert bodies[1]["messages"][-2]["tool_calls"][0]["id"] == "c1"


def test_a_reply_that_already_carries_speech_costs_only_one_call():
    """A tool call alongside content needs no round trip - the buyer has already
    been spoken to, and a second inference would be spend for nothing."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=reply(
                content="A colleague will confirm.",
                tool_calls=[
                    {"id": "c1", "function": {"name": ESCALATE_TOOL, "arguments": "{}"}}
                ],
            ),
        )

    backend = live(handler)
    result = backend.reply(request(fixture()))
    assert result.text == "A colleague will confirm."
    assert result.tools == (ESCALATE_TOOL,)
    assert calls == 1


def test_the_round_trip_happens_once_and_does_not_loop():
    """A model that answers a tool result with another tool call has produced no
    speech for this turn, and the honest record of that is an empty reply."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=reply(
                content="",
                tool_calls=[
                    {
                        "id": f"c{calls}",
                        "function": {"name": ESCALATE_TOOL, "arguments": "{}"},
                    }
                ],
            ),
        )

    backend = live(handler)
    result = backend.reply(request(fixture()))
    assert result.text == ""
    assert result.tools == (ESCALATE_TOOL,)
    assert calls == 2


# --- live: failures are never silent -------------------------------------


def test_a_provider_error_names_the_status_and_never_the_key():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="insufficient credits")

    with pytest.raises(BackendError) as raised:
        live(handler).reply(request(fixture()))
    assert "402" in str(raised.value)
    assert "insufficient credits" in str(raised.value)
    assert "test-key" not in str(raised.value)


def test_an_unparseable_response_is_an_error_not_an_empty_reply():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(BackendError, match="unparseable"):
        live(handler).reply(request(fixture()))


def test_a_failure_on_the_second_inference_is_reported_as_such():
    """Falling back to the empty first reply here would record an escalation as
    a silent turn - the exact thing the round trip exists to prevent."""
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=reply(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "function": {"name": ESCALATE_TOOL, "arguments": "{}"},
                        }
                    ],
                ),
            )
        return httpx.Response(500, text="upstream down")

    with pytest.raises(BackendError, match="post-tool-call"):
        live(handler).reply(request(fixture()))


def test_a_transport_failure_is_a_backend_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(BackendError, match="live call failed"):
        live(handler).reply(request(fixture()))


def test_every_tool_the_harness_offers_has_a_result_to_feed_back():
    """A tool with no result string would send the model an unhelpful generic
    line on the one turn the buyer actually hears."""
    assert {t["function"]["name"] for t in TOOL_SCHEMAS} == set(TOOL_RESULTS)
    assert all(text.strip() for text in TOOL_RESULTS.values())
