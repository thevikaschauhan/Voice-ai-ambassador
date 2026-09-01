"""Where a model reply comes from: a fixture, or the real model.

The two backends are the two modes, and the seam between them is deliberately
one method wide. Everything downstream - the guardrails, the recovery policy,
the assertions, the report - is identical, so a case cannot pass offline for a
reason that would not also hold live.

## The live backend does not go through the LiveKit plugin

It cannot: the harness runs headless with no voice stack (ADR-002), and the
plugin is a voice-session object. So this is a plain non-streaming
`POST /chat/completions` against the same base URL, the same model, the same
`temperature=0` and the same `reasoning: {enabled: false}` the adapter
configures in `adapter/llm_openrouter.build_llm`. What it therefore does NOT
measure is anything the plugin or the stream does: sentence-by-sentence
interception timing, the flush behaviour, prompt caching. Those are the
adapter's tests and the live smoke's job, not this harness's, and the report
says so rather than letting a green matrix imply it.

`ambassador.prompts` is the prompt under test, and `serialise_for_prompt` is
the inventory block - the real ones, not a copy. A prompt edit is a code change
(docs/05-) and this is what makes that enforceable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ambassador.schemas import Language

from .cases import ModelFixture

# The tools the ambassador exposes mid-turn, mirroring the `@function_tool`
# methods on `adapter.agent.AmbassadorAgent`. The names are what the categories
# assert on, and `test_evals_tools.py` pins them against the agent's own so a
# rename there cannot leave this harness measuring a tool that no longer
# exists. The descriptions are shortened from the agent's docstrings on purpose
# - the agent's are the product prompt, these are enough for the model to pick
# the right tool in a single-turn eval.
ESCALATE_TOOL = "escalate_to_human"
BOOKING_TOOL = "offer_booking"

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": ESCALATE_TOOL,
            "description": (
                "Notify a human ambassador so they pick this buyer up. Call this - "
                "do not merely mention a colleague - whenever the buyer asks about "
                "a project not in the inventory, asks the price of a branded "
                "collection, asks for a computation not listed, asks about "
                "availability, wants to negotiate, raises contractual or legal "
                "terms, asks for a person, or complains."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the escalation is needed, in a few words.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": BOOKING_TOOL,
            "description": "Offer the buyer a viewing or a call with an ambassador.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_description": {
                        "type": "string",
                        "description": "The slot in the buyer's own words, for read-back.",
                    }
                },
                "required": ["slot_description"],
            },
        },
    },
]


# What each tool returns to the model, mirrored from the `@function_tool`
# methods on `adapter.agent.AmbassadorAgent`. The framework hands a tool's
# return value back and runs a SECOND inference, which is where the speech for a
# tool-using turn comes from - so a harness that skips that round trip records
# an escalation as a turn that said nothing.
#
# Only the second turn's phrasing depends on these strings; no assertion reads
# them. `offer_booking`'s echoes the slot, which this harness does not track, so
# it is generic - the booking tool is not asserted on by any category.
TOOL_RESULTS: dict[str, str] = {
    ESCALATE_TOOL: (
        "An ambassador has been notified and will pick this up. "
        "Tell the buyer a colleague will confirm this directly."
    ),
    BOOKING_TOOL: (
        "Slot noted. Read it back to the buyer and ask them to confirm."
    ),
}


@dataclass(frozen=True)
class ModelReply:
    text: str
    tools: tuple[str, ...] = ()
    # Which fixture provenance this reply carries, so the report can tally how
    # much of a pass rate is evidence about the model and how much is evidence
    # about the pipeline. Live replies are `recorded` by definition.
    source: str = "recorded"
    intent: str = "compliant"


class BackendError(RuntimeError):
    """The backend could not produce a reply. Never swallowed: a case that did
    not run fails, because a pass rate that quietly excludes the cases nobody
    could answer is worse than no number at all."""


@dataclass(frozen=True)
class ModelRequest:
    # The case's id and language, not the case. A backend needs to say WHICH
    # request failed and WHICH language it was in, and nothing else off an
    # `EvalCase` - carrying the whole object made the eval harness the only
    # thing that could ever call one, which is why text mode could not reuse
    # this pipeline without fabricating a case to satisfy a validator.
    case_id: str
    language: Language
    system_prompt: str
    # (role, content) pairs, oldest first, ending with the buyer turn to answer.
    messages: tuple[tuple[str, str], ...]
    fixture: ModelFixture | None
    # Set on the one regeneration the recovery policy allows, carrying the
    # violation the first reply was blocked for (docs/01-).
    regeneration_detail: str | None = None


class ModelBackend(Protocol):
    name: str

    def reply(self, request: ModelRequest) -> ModelReply: ...


class FixtureBackend:
    """Offline mode. Replays the reply recorded or authored beside the case."""

    name = "offline"

    def reply(self, request: ModelRequest) -> ModelReply:
        fixture = request.fixture
        if fixture is None:
            raise BackendError(
                f"{request.case_id}: no model fixture for this turn. Offline mode "
                "has nothing to replay, and a case that cannot run must fail "
                "rather than disappear from the denominator."
            )
        if request.regeneration_detail is not None:
            if fixture.retry is None:
                raise BackendError(
                    f"{request.case_id}: the first reply was blocked and this "
                    "fixture authors no `retry`, so the regeneration the "
                    "recovery policy allows cannot be replayed."
                )
            fixture = fixture.retry
        return ModelReply(
            text=fixture.text,
            tools=tuple(fixture.tools),
            source=fixture.source,
            intent=fixture.intent,
        )


# Matches adapter/llm_openrouter.py's own timeouts closely enough that a slow
# provider looks the same here as it does on a call, without the streaming
# read window a non-streaming request does not need.
_TIMEOUT = httpx.Timeout(connect=15.0, read=90.0, write=15.0, pool=15.0)


class LiveBackend:
    """Live mode. Calls the real model behind the real ambassador prompt.

    Costs money and varies between runs, which is why it is behind an explicit
    flag and scoped to named categories. `temperature=0` because the runbook
    wants three consecutive identical runs, the same reason the adapter sets it.
    """

    name = "live"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        thinking_disabled: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise BackendError(
                "live mode needs OPENROUTER_API_KEY in agent/.env. Nothing was "
                "called and no spend was incurred."
            )
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._thinking_disabled = thinking_disabled
        self._client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        self.calls = 0

    def close(self) -> None:
        self._client.close()

    def reply(self, request: ModelRequest) -> ModelReply:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.system_prompt}
        ]
        messages.extend(
            {"role": role, "content": content} for role, content in request.messages
        )
        if request.regeneration_detail is not None:
            messages.append(
                {"role": "system", "content": request.regeneration_detail}
            )
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "temperature": 0.0,
        }
        if self._thinking_disabled:
            # ADR-016's trap: reasoning is on by default for this model, and
            # thinking tokens run before any reply.
            body["reasoning"] = {"enabled": False}

        self.calls += 1
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"{request.case_id}: live call failed: {exc}") from exc
        if response.status_code >= 400:
            # The body carries the provider's reason (rate limit, credit
            # balance) and no credential.
            raise BackendError(
                f"{request.case_id}: live call returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise BackendError(
                f"{request.case_id}: unparseable live response: {exc}"
            ) from exc

        calls = message.get("tool_calls") or []
        tools = tuple(
            name
            for name in (call.get("function", {}).get("name", "") for call in calls)
            if name
        )
        text = message.get("content") or ""

        if calls and not text.strip():
            # A tool-call-only reply. The framework hands each tool's return
            # value back and runs a second inference, and THAT is the turn the
            # buyer hears - so stopping here would record an escalation as a
            # turn that said nothing, and every escalation category (five of the
            # gated ten) would report silence. Measured live on an Arabic "I
            # want to speak to a real person": the first inference returned
            # escalate_to_human with empty content.
            #
            # One round trip only. A model that answers the tool result with
            # another tool call has produced no speech for this turn, and the
            # honest record of that is an empty reply, not a loop.
            text = self._after_tool_calls(request, messages, message, calls)

        return ModelReply(
            text=text,
            tools=tools,
            source="recorded",
            intent="compliant",
        )

    def _after_tool_calls(
        self,
        request: ModelRequest,
        messages: list[dict[str, Any]],
        message: dict[str, Any],
        calls: list[dict[str, Any]],
    ) -> str:
        """The second inference, with each tool's return value fed back."""
        followup = [*messages, message]
        for call in calls:
            name = call.get("function", {}).get("name", "")
            followup.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": TOOL_RESULTS.get(
                        name, "Done. Continue the conversation normally."
                    ),
                }
            )
        body: dict[str, Any] = {
            "model": self._model,
            "messages": followup,
            "tools": TOOL_SCHEMAS,
            "temperature": 0.0,
        }
        if self._thinking_disabled:
            body["reasoning"] = {"enabled": False}
        self.calls += 1
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise BackendError(
                f"{request.case_id}: the post-tool-call inference failed: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise BackendError(
                f"{request.case_id}: the post-tool-call inference returned "
                f"{response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()["choices"][0]["message"].get("content") or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise BackendError(
                f"{request.case_id}: unparseable post-tool-call response: {exc}"
            ) from exc
