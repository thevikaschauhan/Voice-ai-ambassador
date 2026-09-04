"""The analysis asks for "project ids" and never says what they are.

god re-ran the post-#134 finaliser against the human's real 08:32Z transcript
rebuilt from the store. The first attempt now VALIDATES - question_turn_indexes
[4,5,6], project_named on turn 3 - and then `_score` raises

    project_ids: Binghatti Skyrise, Binghatti Aquarise, Binghatti Circle
    do not resolve in inventory

because `_ANALYSIS_INSTRUCTION` says "project_ids (a list of project ids
mentioned)" and never lists them. `brief.py:_SYSTEM` does exactly the opposite:
it interpolates `project_ids=` and tells the model to use ids from that list
only. The model returned the names it was shown in the conversation, which is
the only reading available to it.

Two failures in one: the instruction is unanswerable, and the failure it
produces is filed as `evidence` - the code for a model citing a turn that never
happened - which sends an operator looking at the transcript instead of at the
prompt.

Imports inside each test so RED reads N failed = N cases.
"""

from __future__ import annotations

import pytest

# What the model actually returned on the human's call.
NAMES = ["Binghatti Skyrise", "Binghatti Aquarise", "Binghatti Circle"]
IDS = ["binghatti-skyrise", "binghatti-aquarise", "binghatti-circle"]


def test_the_names_the_model_returned_resolve_to_ids() -> None:
    """The defect, at the layer that raised. Resolution is deterministic - an
    inventory lookup, not a model call and not a guess."""
    from test_analysis_finaliser import _draft, _snapshot

    from adapter.analysis import _score
    from ambassador.schemas import SignalEvidence

    draft = _draft(
        project_named=SignalEvidence(observed=True, turn_indexes=[1]),
        project_ids=NAMES,
    )

    _total, project_ids = _score(_snapshot("sess_test"), draft)

    assert project_ids == IDS


def test_the_resolution_does_not_care_about_case_or_spacing() -> None:
    """A model that lower-cases a name has not made a different claim."""
    from test_analysis_finaliser import _draft, _snapshot

    from adapter.analysis import _score
    from ambassador.schemas import SignalEvidence

    draft = _draft(
        project_named=SignalEvidence(observed=True, turn_indexes=[1]),
        project_ids=["  binghatti skyrise ", "BINGHATTI CIRCLE"],
    )

    _total, project_ids = _score(_snapshot("sess_test"), draft)

    assert project_ids == ["binghatti-skyrise", "binghatti-circle"]


def test_an_id_the_model_returns_directly_still_resolves() -> None:
    """A GUARD, passing before and after: the ids were always the contract, and
    accepting names must not stop accepting ids."""
    from test_analysis_finaliser import _draft, _snapshot

    from adapter.analysis import _score
    from ambassador.schemas import SignalEvidence

    draft = _draft(
        project_named=SignalEvidence(observed=True, turn_indexes=[1]),
        project_ids=["binghatti-skyrise"],
    )

    _total, project_ids = _score(_snapshot("sess_test"), draft)

    assert project_ids == ["binghatti-skyrise"]


def test_an_invented_project_still_fails_and_says_so_in_its_own_terms() -> None:
    """Resolution must not become acceptance. An id nobody sells is still a
    validation failure - and now one an operator can tell apart from a model
    citing a turn that never happened."""
    from test_analysis_finaliser import _draft, _snapshot

    from adapter.analysis import _score
    from adapter.persist import _failure_code
    from ambassador.schemas import SignalEvidence

    draft = _draft(
        project_named=SignalEvidence(observed=True, turn_indexes=[1]),
        project_ids=["Binghatti Moonrise"],
    )

    with pytest.raises(ValueError) as raised:
        _score(_snapshot("sess_test"), draft)

    assert _failure_code(raised.value) == "unknown_project"


def test_the_instruction_names_every_inventory_id() -> None:
    """Like `brief.py:_SYSTEM`, which passes `project_ids=` and says "from this
    list only". A field called project_ids with no list beside it is a question
    the model cannot answer."""
    from adapter.analysis import analysis_instruction
    from ambassador.inventory import load_inventory

    instruction = analysis_instruction(repair=False)

    for project in load_inventory():
        assert project.id in instruction, project.id


def test_the_instruction_says_what_to_return_when_no_project_was_named() -> None:
    """The null-safe half. The brief defect on this same call was the model
    reading "use null when the buyer did not say" as being about the fields;
    an empty list has to be named explicitly or `null` is the obvious guess."""
    from adapter.analysis import analysis_instruction

    instruction = analysis_instruction(repair=False).lower()

    assert "empty list" in instruction or "[]" in instruction


def test_the_repair_instruction_carries_the_ids_too() -> None:
    """The repair is the attempt that follows a rejection, so it is the one that
    can least afford to be missing the list."""
    from adapter.analysis import analysis_instruction
    from ambassador.inventory import load_inventory

    repair = analysis_instruction(repair=True)

    for project in load_inventory():
        assert project.id in repair, project.id


def test_a_value_with_nothing_in_it_resolves_to_nothing() -> None:
    """An empty string is not a project, and neither is punctuation.

    It is also what a model sends when it half-answers a list, so it has to fail
    the same way an invented name does rather than quietly disappear on the way
    to the Projects column. Called without an inventory argument on purpose:
    the default is the production one.
    """
    from ambassador.inventory import resolve_project_id

    assert resolve_project_id("") is None
    assert resolve_project_id("   --  ") is None
    assert resolve_project_id("binghatti-skyrise") == "binghatti-skyrise"
