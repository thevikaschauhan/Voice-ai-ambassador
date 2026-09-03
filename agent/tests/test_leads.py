"""The interest score: seven signals, all arithmetic in code, no model maths.

P2-S03's RED test lives here. The rule this file exists to hold is ADR-020's:
the model supplies summary text, boolean signals and supporting turn indexes,
and never a number. Everything numeric is computed here from a versioned
rubric, which is the same invariant as "the model never does arithmetic"
applied to a second surface - a score a model returns is a figure nobody can
check, and unlike a price there is no inventory to check it against.

Imports are inside the tests on purpose. `ambassador.leads` does not exist at
the RED commit, and a module-level import would make this file one collection
error rather than one failure per case - which hides how much is actually being
specified and makes the RED commit unreadable as a specification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

DATA = Path(__file__).resolve().parents[2] / "data"

# The documented maxima, from docs/02- and docs/10-. Written here as literals
# rather than read from the rubric: a test that reads the same file as the code
# proves only that one file was read twice.
DOCUMENTED_MAXIMA = {
    "budget_stated": 15,
    "project_named": 15,
    "timeline_stated": 10,
    "contact_shared": 20,
    "viewing_or_human_requested": 25,
    "questions_asked": 10,
    "call_length": 5,
}


def write_rubric(tmp_path: Path, body: dict) -> Path:
    path = tmp_path / "interest-score.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def a_rubric(**overrides) -> dict:
    body = {
        "version": "test-1",
        "maximum": 100,
        "weights": dict(DOCUMENTED_MAXIMA),
    }
    body.update(overrides)
    return body


def only(signal: str, *, turns: list[int] | None = None, seconds: int = 0):
    """Inputs with exactly one signal observed, and nothing else.

    The scorer's whole job is that a signal contributes its own points and no
    others', so every case here turns on one thing at a time.
    """
    from ambassador.leads import ScoringInputs
    from ambassador.schemas import ContactCapture, LeadAnalysisDraft, SignalEvidence

    off = SignalEvidence(observed=False, turn_indexes=[])
    on = SignalEvidence(observed=True, turn_indexes=turns or [1])

    draft = LeadAnalysisDraft(
        summary="a summary",
        budget_stated=on if signal == "budget_stated" else off,
        project_named=on if signal == "project_named" else off,
        project_ids=["binghatti-skyrise"] if signal == "project_named" else [],
        timeline_stated=on if signal == "timeline_stated" else off,
        viewing_or_human_requested=(
            on if signal == "viewing_or_human_requested" else off
        ),
        question_turn_indexes=turns or [] if signal == "questions_asked" else [],
    )
    contact = (
        ContactCapture(
            status="captured",
            asked_turn_index=1,
            source_turn_index=1,
            name="A Buyer",
            phone="+971500000000",
            contact_permission=True,
            confirmed=True,
        )
        if signal == "contact_shared"
        else ContactCapture(status="not_asked")
    )
    started = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    return ScoringInputs(
        draft=draft,
        contact=contact,
        started_at=started,
        ended_at=started + timedelta(seconds=seconds),
        buyer_turn_indexes=[1, 2, 3, 4],
        project_ids_in_inventory=["binghatti-skyrise"],
    )


# --- the named RED test ---------------------------------------------------


@pytest.mark.parametrize("signal", sorted(DOCUMENTED_MAXIMA))
def test_every_rubric_signal_contributes_only_its_documented_points(signal):
    """One signal on, nothing else: the total is that signal's documented
    maximum and every other line scores zero.

    This is the test the whole card exists to satisfy, and it is deliberately
    stronger than "the total looks right". A scorer that added a signal's
    points twice, or credited a neighbour, or silently clamped a total, all
    produce plausible totals; only checking the per-signal breakdown catches
    them. It also pins the maxima against the DOCUMENT rather than against the
    rubric file, so a weight edit that nobody meant fails here instead of
    quietly rescoring every future lead.
    """
    from ambassador.leads import load_rubric, score_interest

    rubric = load_rubric()
    maximum = DOCUMENTED_MAXIMA[signal]

    # Enough turns and seconds that the two counted signals reach their caps.
    inputs = only(signal, turns=[1, 2, 3], seconds=600)
    score = score_interest(inputs, rubric)

    assert score.total == maximum, (signal, score.breakdown)

    awarded = {item.signal: item.points_awarded for item in score.breakdown}
    assert awarded[signal] == maximum
    assert all(points == 0 for name, points in awarded.items() if name != signal)
    assert set(awarded) == set(DOCUMENTED_MAXIMA)


def test_the_documented_maxima_are_what_the_shipped_rubric_carries():
    """The rubric file and the contract are one decision written twice, so they
    are checked against each other. A weight changed in the file without the
    document is how a score silently stops meaning what docs/10- says."""
    from ambassador.leads import load_rubric

    rubric = load_rubric()
    assert rubric.weights == DOCUMENTED_MAXIMA
    assert rubric.maximum == 100


def test_all_seven_signals_together_reach_exactly_the_maximum():
    """The complement of the parametrised test: the parts sum to the whole.

    Worth its own case because "each signal alone scores its max" and "the
    maximum is 100" can both hold while the total is capped rather than
    summed, which would make a perfect lead and a nearly-perfect one
    indistinguishable.
    """
    from ambassador.leads import ScoringInputs, load_rubric, score_interest
    from ambassador.schemas import ContactCapture, LeadAnalysisDraft, SignalEvidence

    on = SignalEvidence(observed=True, turn_indexes=[1])
    started = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    inputs = ScoringInputs(
        draft=LeadAnalysisDraft(
            summary="everything at once",
            budget_stated=on,
            project_named=on,
            project_ids=["binghatti-skyrise"],
            timeline_stated=on,
            viewing_or_human_requested=on,
            question_turn_indexes=[1, 2],
        ),
        contact=ContactCapture(
            status="captured",
            asked_turn_index=1,
            source_turn_index=1,
            name="A Buyer",
            phone="+971500000000",
            contact_permission=True,
            confirmed=True,
        ),
        started_at=started,
        ended_at=started + timedelta(minutes=9),
        buyer_turn_indexes=[1, 2, 3],
        project_ids_in_inventory=["binghatti-skyrise"],
    )
    assert score_interest(inputs, load_rubric()).total == 100


# --- the counted signals, which are where arithmetic can go wrong ----------


@pytest.mark.parametrize(
    ("turns", "expected"),
    [([], 0), ([1], 5), ([1, 2], 10), ([1, 2, 3], 10), ([1, 1, 2], 10)],
)
def test_questions_earn_five_each_capped_at_two_and_counted_distinctly(turns, expected):
    """Five points per DISTINCT validated buyer turn, capped at two.

    The repeated-index case is the one that matters: a model listing the same
    turn twice must not buy the cap with one question.
    """
    from ambassador.leads import load_rubric, score_interest

    score = score_interest(only("questions_asked", turns=turns), load_rubric())
    assert score.total == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, 0), (59, 0), (60, 1), (119, 1), (300, 5), (3600, 5)],
)
def test_call_length_earns_one_point_per_complete_minute_capped_at_five(
    seconds, expected
):
    """COMPLETE minutes, so 59 seconds is worth nothing. Duration comes from
    the timestamps rather than anything the model said."""
    from ambassador.leads import load_rubric, score_interest

    score = score_interest(only("call_length", seconds=seconds), load_rubric())
    assert score.total == expected


def test_a_negative_duration_is_refused_rather_than_scored():
    """Clocks and a truncated call can produce an end before a start. The
    scorer clamps nothing silently (docs/10-), so this fails validation rather
    than scoring zero and looking like a short call."""
    from ambassador.leads import score_interest, load_rubric

    inputs = only("call_length", seconds=-30)
    with pytest.raises(ValueError, match="ended_at"):
        score_interest(inputs, load_rubric())


# --- evidence has to be real ----------------------------------------------


def test_evidence_turn_indexes_must_exist_in_the_snapshot():
    """A model citing a turn that never happened is the failure this check
    exists for: without it, an invented index is indistinguishable from a real
    one and the evidence trail in the admin UI points at nothing."""
    from ambassador.leads import load_rubric, score_interest

    inputs = only("budget_stated", turns=[99])
    with pytest.raises(ValueError, match="99"):
        score_interest(inputs, load_rubric())


def test_a_question_index_that_is_not_a_buyer_turn_is_refused():
    """Same rule on the counted signal, where an invented index is not just
    unverifiable but worth points."""
    from ambassador.leads import load_rubric, score_interest

    inputs = only("questions_asked", turns=[1, 77])
    with pytest.raises(ValueError, match="77"):
        score_interest(inputs, load_rubric())


def test_a_named_project_must_resolve_to_inventory():
    """docs/02-: every id in `project_ids` resolves through the inventory
    loader. A brochure name the model recognised and inventory does not is the
    unknown-project case, and it must not earn points for being named."""
    from ambassador.leads import ScoringInputs, load_rubric, score_interest
    from ambassador.schemas import ContactCapture, LeadAnalysisDraft, SignalEvidence

    started = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    inputs = ScoringInputs(
        draft=LeadAnalysisDraft(
            summary="s",
            budget_stated=SignalEvidence(observed=False, turn_indexes=[]),
            project_named=SignalEvidence(observed=True, turn_indexes=[1]),
            project_ids=["binghatti-atlantis"],
            timeline_stated=SignalEvidence(observed=False, turn_indexes=[]),
            viewing_or_human_requested=SignalEvidence(observed=False, turn_indexes=[]),
            question_turn_indexes=[],
        ),
        contact=ContactCapture(status="not_asked"),
        started_at=started,
        ended_at=started,
        buyer_turn_indexes=[1],
        project_ids_in_inventory=["binghatti-skyrise"],
    )
    with pytest.raises(ValueError, match="binghatti-atlantis"):
        score_interest(inputs, load_rubric())


def test_project_ids_are_empty_exactly_when_the_signal_is_not_observed():
    """Stated as a contract in docs/02- and enforced on the model's own output,
    because both directions are wrong in different ways: ids without the signal
    is a claim nobody made, and the signal without ids is unverifiable."""
    from ambassador.schemas import LeadAnalysisDraft, SignalEvidence
    from pydantic import ValidationError

    off = SignalEvidence(observed=False, turn_indexes=[])
    base = dict(
        summary="s",
        budget_stated=off,
        timeline_stated=off,
        viewing_or_human_requested=off,
        question_turn_indexes=[],
    )
    with pytest.raises(ValidationError, match="project_ids"):
        LeadAnalysisDraft(**base, project_named=off, project_ids=["binghatti-skyrise"])
    with pytest.raises(ValidationError, match="project_ids"):
        LeadAnalysisDraft(
            **base,
            project_named=SignalEvidence(observed=True, turn_indexes=[1]),
            project_ids=[],
        )


def test_an_observed_signal_must_cite_at_least_one_turn():
    """An observation with no evidence is an assertion. The admin surface shows
    the cited turns as the reason a lead scored what it did."""
    from ambassador.schemas import SignalEvidence
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="turn_indexes"):
        SignalEvidence(observed=True, turn_indexes=[])


# --- the rubric loader ----------------------------------------------------


def test_the_shipped_rubric_is_the_one_the_code_loads_by_default():
    from ambassador.leads import load_rubric

    assert (DATA / "interest-score.yaml").exists()
    assert load_rubric().version == load_rubric(DATA / "interest-score.yaml").version


def test_an_unknown_signal_is_refused(tmp_path):
    """A weight for a signal the scorer does not compute is points nobody can
    earn, and it silently changes what a total out of 100 means."""
    from ambassador.leads import load_rubric

    body = a_rubric()
    body["weights"]["mood"] = 0
    with pytest.raises(ValueError, match="mood"):
        load_rubric(write_rubric(tmp_path, body))


def test_a_missing_signal_is_refused(tmp_path):
    """All seven exactly once (docs/02-). A dropped signal makes every future
    lead score lower for a reason no reader could see."""
    from ambassador.leads import load_rubric

    body = a_rubric()
    del body["weights"]["call_length"]
    with pytest.raises(ValueError, match="call_length"):
        load_rubric(write_rubric(tmp_path, body))


def test_weights_that_do_not_sum_to_the_declared_maximum_are_refused(tmp_path):
    """The bound is what makes "out of 100" true. Without it a rubric can
    declare 100 and be unreachable, or exceed it and produce a total nobody
    can interpret."""
    from ambassador.leads import load_rubric

    body = a_rubric()
    body["weights"]["budget_stated"] = 16
    with pytest.raises(ValueError, match="101"):
        load_rubric(write_rubric(tmp_path, body))


def test_a_maximum_other_than_one_hundred_is_refused(tmp_path):
    """docs/10- fixes it at 100 so a score is comparable across rubric
    versions. A rubric out of 50 makes historic and current leads read the
    same and mean different things."""
    from ambassador.leads import load_rubric

    body = a_rubric(
        maximum=50, weights={k: v // 2 for k, v in DOCUMENTED_MAXIMA.items()}
    )
    with pytest.raises(ValueError, match="100"):
        load_rubric(write_rubric(tmp_path, body))


@pytest.mark.parametrize("weight", [-1, "fifteen", None, 1.5])
def test_a_weight_that_is_not_a_whole_non_negative_number_is_refused(tmp_path, weight):
    """Points are whole and non-negative. A float would make totals
    irreproducible across versions and a negative would let a signal subtract,
    which no rubric in docs/10- describes."""
    from ambassador.leads import load_rubric

    body = a_rubric()
    body["weights"]["timeline_stated"] = weight
    with pytest.raises(ValueError):
        load_rubric(write_rubric(tmp_path, body))


def test_a_rubric_with_no_version_is_refused(tmp_path):
    """The version is what makes a historic score interpretable after the
    weights change, so a rubric without one cannot be used at all."""
    from ambassador.leads import load_rubric

    body = a_rubric()
    del body["version"]
    with pytest.raises(ValueError, match="version"):
        load_rubric(write_rubric(tmp_path, body))


def test_the_score_records_the_rubric_version_that_produced_it(tmp_path):
    """Historic scores are not recomputed when weights change (ADR-020), so the
    number is only interpretable beside the version that produced it."""
    from ambassador.leads import load_rubric, score_interest

    rubric = load_rubric(write_rubric(tmp_path, a_rubric(version="rubric-2027-01")))
    score = score_interest(only("budget_stated"), rubric)
    assert score.score_version == "rubric-2027-01"


def test_a_score_carries_every_signal_in_its_breakdown_even_at_zero(tmp_path):
    """The breakdown is the explanation, and a signal missing from it reads as
    "not part of the rubric" rather than "scored nothing". docs/10- calls the
    score explainable; this is what that means concretely."""
    from ambassador.leads import load_rubric, score_interest

    score = score_interest(only("budget_stated"), load_rubric())
    assert [item.signal for item in score.breakdown] == list(DOCUMENTED_MAXIMA)
    for item in score.breakdown:
        assert item.max_points == DOCUMENTED_MAXIMA[item.signal]


def test_contact_without_permission_or_a_value_scores_nothing():
    """docs/10-: the points need a valid contact value AND contact permission.
    A captured name with no way to reach anyone is not a shared contact, and a
    number retained without permission must never earn points."""
    from ambassador.leads import ScoringInputs, load_rubric, score_interest
    from ambassador.schemas import ContactCapture, LeadAnalysisDraft, SignalEvidence

    off = SignalEvidence(observed=False, turn_indexes=[])
    started = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    def scored(contact) -> int:
        return score_interest(
            ScoringInputs(
                draft=LeadAnalysisDraft(
                    summary="s",
                    budget_stated=off,
                    project_named=off,
                    project_ids=[],
                    timeline_stated=off,
                    viewing_or_human_requested=off,
                    question_turn_indexes=[],
                ),
                contact=contact,
                started_at=started,
                ended_at=started,
                buyer_turn_indexes=[1],
                project_ids_in_inventory=["binghatti-skyrise"],
            ),
            load_rubric(),
        ).total

    assert scored(ContactCapture(status="declined")) == 0
    assert scored(ContactCapture(status="not_asked")) == 0
    assert (
        scored(
            ContactCapture(
                status="captured",
                asked_turn_index=1,
                source_turn_index=1,
                name="A Buyer",
                phone="+971500000000",
                contact_permission=False,
                confirmed=True,
            )
        )
        == 0
    )
