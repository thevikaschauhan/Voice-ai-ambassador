"""The harness's tool schemas against the agent's own function tools.

The escalation categories all turn on one string, `escalate_to_human`. If the
agent renames or drops a tool and this harness keeps offering the old name, live
mode measures a tool that no longer exists and every escalation row goes quietly
red - or worse, the harness offers a name the agent does not have and the model
calls it, which reads as an escalation nobody was notified of.

The agent needs the voice stack, so this file skips without it (ADR-002: the
core stays testable with no framework installed).
"""

from __future__ import annotations

import pytest

pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from evals.backends import BOOKING_TOOL, ESCALATE_TOOL, TOOL_SCHEMAS  # noqa: E402


def _agent_tool_names() -> set[str]:
    """The `@function_tool` methods on the agent, by the name the model sees."""
    from livekit.agents.llm import function_tool as _  # noqa: F401  (import guard)

    from adapter.agent import AmbassadorAgent

    names: set[str] = set()
    for attribute in dir(AmbassadorAgent):
        candidate = getattr(AmbassadorAgent, attribute, None)
        info = getattr(candidate, "__livekit_tool_info", None)
        if info is not None:
            names.add(getattr(info, "name", attribute) or attribute)
        elif callable(candidate) and getattr(candidate, "__livekit_tool__", False):
            names.add(attribute)
    return names


def test_the_harness_offers_exactly_the_tools_the_agent_has():
    agent_tools = _agent_tool_names()
    assert agent_tools, (
        "no @function_tool methods found on AmbassadorAgent - if the framework "
        "changed how it marks them, this detection has to change with it rather "
        "than be deleted, or the parity check silently passes on an empty set"
    )
    harness_tools = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert harness_tools == agent_tools


def test_the_escalation_tool_name_is_the_one_the_assertions_read():
    assert ESCALATE_TOOL == "escalate_to_human"
    assert BOOKING_TOOL == "offer_booking"
    assert ESCALATE_TOOL in {s["function"]["name"] for s in TOOL_SCHEMAS}


def test_every_schema_is_shaped_for_an_openai_compatible_tools_array():
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] and function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert parameters["required"]
        for name in parameters["required"]:
            assert name in parameters["properties"]
