"""The analysis request has to be shaped like the brief request.

god's live probe, same key and endpoint: the brief-shaped body (stream false,
temperature 0, max_tokens 600, `response_format` json_object, reasoning
disabled) returns in 1.9-2.4s on qwen3.7-flash. The body `analysis_ask` sent -
model and messages only - took **16.3 seconds and burned 1982 reasoning
tokens** on the same model, because leaving the body bare leaves THINKING ON.

So the 2.0s per-request timeout and the 4s cap from #131 failed on every call.
I reused the brief extractor's client and its key and never its request SHAPE,
and the shape is where the discipline lived.

No live vendor calls here: the body is a pure function of settings and prompt,
which is also what makes the discipline reviewable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

# god's measurements, per model, from the card. The numbers the timeout is
# sized against rather than a guess.
MEASURED_COMMON_CASE_MAX = 2.4  # qwen3.7-flash and gemini-2.5-flash
MEASURED_OUTLIER = 3.9  # gemini-2.5-flash-lite, once


def _settings(**overrides):
    from test_agent import make_settings

    base = dict(
        openrouter_api_key="k",
        analysis_model="qwen/qwen3.7-flash",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_thinking="off",
    )
    base.update(overrides)
    return make_settings(**base)


def test_the_body_carries_the_brief_shaped_discipline() -> None:
    from adapter.analysis import analysis_body

    body = analysis_body(_settings(), "turn 1 buyer: hello", repair=False)

    assert body["stream"] is False
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert isinstance(body["max_tokens"], int) and body["max_tokens"] > 0


def test_thinking_is_disabled_when_the_setting_says_so() -> None:
    """The 16.3s / 1982 reasoning tokens, in one field. ADR-016 keeps thinking
    off the voice path; this call is off the latency path but still inside a
    shutdown budget, so it burns the seal's margin instead."""
    from adapter.analysis import analysis_body

    body = analysis_body(_settings(llm_thinking="off"), "p", repair=False)

    assert body["reasoning"] == {"enabled": False}


def test_thinking_stays_on_when_the_operator_turned_it_on() -> None:
    """The same `thinking_disabled` setting the brief extractor honours - not a
    hardcoded false. An operator who turns thinking on has said something."""
    from adapter.analysis import analysis_body

    body = analysis_body(_settings(llm_thinking="on"), "p", repair=False)

    assert "reasoning" not in body


def test_the_first_attempt_fits_the_measured_common_case() -> None:
    """A timeout under the measured maximum is a timeout that fails on a
    healthy call."""
    from adapter.analysis import REQUEST_TIMEOUT_SECONDS

    assert REQUEST_TIMEOUT_SECONDS > MEASURED_COMMON_CASE_MAX


def test_a_first_attempt_and_a_repair_both_fit_the_cap() -> None:
    """docs/10- allows one repair. If two attempts cannot fit the analysis cap,
    the repair is a promise the budget cannot keep."""
    from adapter.agent import ANALYSIS_BUDGET_SECONDS
    from adapter.analysis import REQUEST_TIMEOUT_SECONDS

    assert REQUEST_TIMEOUT_SECONDS * 2 <= ANALYSIS_BUDGET_SECONDS


def test_the_analysis_cap_still_fits_the_lead_section() -> None:
    """The stages share one deadline, so their ceilings may sum above it - but
    no single stage may exceed the deadline itself, or it could starve the
    others completely."""
    from adapter.agent import ANALYSIS_BUDGET_SECONDS, LEAD_SECTION_BUDGET_SECONDS

    assert ANALYSIS_BUDGET_SECONDS < LEAD_SECTION_BUDGET_SECONDS


def test_the_repair_body_is_the_same_shape_as_the_first() -> None:
    """A repair that dropped `response_format` would ask a thinking model for
    prose at the one moment there is least time for it."""
    from adapter.analysis import analysis_body

    first = analysis_body(_settings(), "p", repair=False)
    repair = analysis_body(_settings(), "p", repair=True)

    for field in (
        "stream",
        "temperature",
        "max_tokens",
        "response_format",
        "reasoning",
    ):
        assert repair[field] == first[field], field
    # Only the instruction differs.
    assert repair["messages"][0]["content"] != first["messages"][0]["content"]


def test_the_prompt_is_the_user_message_and_nothing_else_is_added() -> None:
    from adapter.analysis import analysis_body

    body = analysis_body(_settings(), "turn 1 buyer: hello", repair=False)

    assert body["messages"][-1] == {"role": "user", "content": "turn 1 buyer: hello"}
    assert body["model"] == "qwen/qwen3.7-flash"
