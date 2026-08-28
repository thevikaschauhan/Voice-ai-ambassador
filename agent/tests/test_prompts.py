"""The ambassador prompt's escalation constraints, held to one shape.

Escalation is a feature (AGENTS.md invariant 5) and the prompt is where it is
requested. Live measurement on day 2 found the shape matters: a constraint that
described escalation in words without naming the tool fired it on one run in
three, and the same constraint with `escalate_to_human` named in its imperative
fired 3/3. Constraint 3 was fixed on that evidence and its siblings were not,
which left constraint 2 - the designed answer to an unlisted computation, per
invariant 2 - describing an escalation the model had no tool name for.

These tests pin the shape, not the wording. No framework import, so they run in
core-only mode.
"""

from __future__ import annotations

import re

from ambassador.prompts import NAIVE_PROMPT, build_ambassador_prompt

TOOL = "escalate_to_human"

# The constraints that route the buyer to a person. Written out rather than
# inferred: which situations escalate is a product decision, and a test that
# derived the set from the text could only ever agree with the text.
ESCALATING = {2, 3, 4, 6, 7}


def _constraints() -> dict[int, str]:
    prompt = build_ambassador_prompt("INVENTORY BLOCK", "en")
    body = prompt.split("ABSOLUTE CONSTRAINTS\n", 1)[1].split("\n\nINVENTORY (", 1)[0]
    numbered = {}
    for line in body.splitlines():
        match = re.match(r"(\d+)\.\s+(.*)", line)
        if match:
            numbered[int(match.group(1))] = match.group(2)
    return numbered


def test_the_constraints_parse():
    # Everything below is vacuous if the split silently returns nothing.
    constraints = _constraints()
    assert set(constraints) == set(range(1, 11))


def test_every_escalating_constraint_names_the_tool_in_an_imperative():
    """Naming it is not enough - it has to be asked for, in the same shape.

    "call the escalate_to_human tool" is the phrasing that measured 3/3 live.
    """
    for number in sorted(ESCALATING):
        text = _constraints()[number]
        assert f"call the {TOOL} tool" in text, f"constraint {number}: {text}"


def test_no_other_constraint_asks_for_the_tool():
    """The escalating set is a reviewed decision, not a default.

    An agent that escalates on everything is as broken as one that never does,
    and the demo turns on the refusal being deliberate.
    """
    for number, text in _constraints().items():
        if number not in ESCALATING:
            assert TOOL not in text, f"constraint {number}: {text}"


def test_the_unlisted_computation_is_an_escalation_not_a_calculation():
    """AGENTS.md invariant 2 names refusal plus escalation as the answer."""
    text = _constraints()[2]
    assert "Never perform arithmetic" in text
    assert "computation that is not listed" in text
    assert f"call the {TOOL} tool" in text


def test_the_product_voice_holds():
    prompt = build_ambassador_prompt("INVENTORY BLOCK", "en")
    assert "!" not in prompt
    # Regulatory, not stylistic (AGENTS.md hard rules).
    for banned in ("guaranteed", "assured return", "risk-free"):
        assert banned not in prompt.lower()


def test_the_naive_prompt_stays_naive():
    """It exists to fail the demo on purpose (docs/03-); giving it the tool or
    a constraint would blunt the comparison."""
    assert TOOL not in NAIVE_PROMPT
    assert "INVENTORY" not in NAIVE_PROMPT
