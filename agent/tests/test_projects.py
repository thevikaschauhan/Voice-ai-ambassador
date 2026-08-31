"""Project-name matching and its confirmation policy (ADR-011, docs/04-).

The suite is in two halves for a reason the budget half of ADR-011 learned the
hard way: eight defects shipped behind green tests because every test asserted
on the returned `Decision` and none drove a conversation. So the matcher gets
table-driven tests over the utterances the recognisers actually produce, and
the policy gets multi-turn exchanges. The turns a BUYER hears are asserted in
test_agent.py, through `llm_node`.
"""

from __future__ import annotations

import pytest

from ambassador.budget import load_currency_vocabulary
from ambassador.inventory import load_inventory
from ambassador.projects import (
    ProjectNamePolicy,
    agreement_words,
    build_name_index,
    match_project_name,
    read_agreement,
)

SKYRISE = "binghatti-skyrise"
AQUARISE = "binghatti-aquarise"
CIRCLE = "binghatti-circle"
BUGATTI = "bugatti-residences"


@pytest.fixture(scope="module")
def index():
    return build_name_index(load_inventory())


@pytest.fixture(scope="module")
def words():
    return agreement_words(load_currency_vocabulary())


def policy(index, words, language: str = "en") -> ProjectNamePolicy:
    return ProjectNamePolicy(index, words, language)


# --- what the recognisers actually return ------------------------------------
#
# Not invented misspellings. ADR-015's bake-off is the source: every model
# tested mangled the client's own name ("Bint Jbeil", "Binghati", "binghati"),
# and OpenRouter's transcription endpoint ignores the biasing parameter, so
# nothing fixes this on the input side. These are the transcripts the policy
# has to survive.


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Tell me about Binghatti Skyrize", SKYRISE),
        ("Binghatti Sky Rise", SKYRISE),
        ("Bint Jbeil Sky Rise please", SKYRISE),
        ("Binghatti Aqua Rise", AQUARISE),
        ("Binghatti Aquarize", AQUARISE),
        ("Binghatti Sircle", CIRCLE),
        ("Binghatti Cirkle", CIRCLE),
    ],
)
def test_a_mangled_name_is_marginal_and_names_the_project(
    index, utterance, expected
):
    match = match_project_name(utterance, index)
    assert match is not None, utterance
    assert match.project_id == expected
    assert match.band == "marginal"


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Tell me about Binghatti Skyrise", SKYRISE),
        ("Binghatti Aquarise", AQUARISE),
        ("Binghatti Circle", CIRCLE),
        ("the Bugatti Residences", BUGATTI),
        # The distinctive half on its own is how buyers actually talk once the
        # brand is established in the conversation.
        ("what does a studio cost at Skyrise", SKYRISE),
        ("Aquarise please", AQUARISE),
        ("Bugatti", BUGATTI),
    ],
)
def test_a_clean_name_is_confident_and_asks_nothing(index, utterance, expected):
    match = match_project_name(utterance, index)
    assert match is not None, utterance
    assert match.project_id == expected
    assert match.band == "confident"


def test_the_two_rise_projects_are_ambiguous_rather_than_confident(index):
    """`Binghatti Skyrise` and `Binghatti Aquarise` differ in one token and
    share an ending. A transcript that lost the distinguishing syllable must
    not resolve to whichever happens to score higher."""
    match = match_project_name("what about Binghatti Rise", index)
    assert match is not None
    assert match.band == "marginal"
    assert match.similarity - match.runner_up < 0.12


# --- and what must NOT match --------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        # The budget policy's own opening utterance. "budget" scores 0.615
        # against "bugatti", which is why the floor sits above it: a lower one
        # turns every budget mention into a project question.
        "My budget is 2 crore",
        "my budget is two million dirhams",
        "do crore ka budget hai",
        # Ordinary questions.
        "what is the payment plan",
        "when does it hand over",
        "is there a swimming pool",
        "can I speak to a person",
        "I want a two bedroom",
        "up to two million",
        "what is the down payment",
        # Replies to a confirmation.
        "yes",
        "No, that's not it",
        "dirhams",
        "can you repeat that",
        # A developer we do not sell. Constraint 3 escalates this; the name
        # policy must not claim it recognised one of ours.
        "Do you have anything in Emaar Beachfront",
    ],
)
def test_an_ordinary_utterance_names_no_project(index, utterance):
    assert match_project_name(utterance, index) is None, utterance


@pytest.mark.parametrize(
    "utterance",
    [
        # `Binghatti Circle` sits in Jumeirah Village Circle. Without the
        # decoy keys the area resolves to the project and the agent asks a
        # question that makes it look broken in front of the client.
        "Jumeirah Village Circle apartments",
        "Circle",
        "I live in Business Bay",
        "anything in Dubai Maritime City",
        # The bare brand word cannot distinguish between four projects, so it
        # is a decoy too. Without it this matched whichever name happened to
        # be nearest, which is `Bugatti Residences` at 0.75.
        "tell me about Binghatti",
    ],
)
def test_an_area_or_the_bare_brand_name_suppresses_the_match(index, utterance):
    assert match_project_name(utterance, index) is None, utterance


def test_the_decoy_does_not_suppress_the_name_it_sits_inside(index):
    """The load-bearing detail of the decoy rule.

    "Binghatti Skyrize" contains `binghatti` exactly, so the decoy scores a
    perfect similarity. Comparing on similarity would suppress the one match
    this module exists for; comparing on coverage does not, because the decoy
    explains one token where the project name explains two.
    """
    assert match_project_name("Binghatti Skyrize", index) is not None


def test_a_rejected_project_is_never_offered_again(index):
    """`exclude` is what stops "did you mean Skyrise?" / "no" / "did you mean
    Skyrise?" going round for ever.

    The excluded project drops out of the ranking rather than out of the
    answer: with Skyrise rejected, the same words are a marginal match for
    Aquarise, and offering the OTHER "-rise" tower is the right next question
    for a buyer who just said the first one was wrong.
    """
    assert match_project_name("Binghatti Skyrise", index).project_id == SKYRISE
    second = match_project_name(
        "Binghatti Skyrise", index, exclude=frozenset({SKYRISE})
    )
    assert second is None or second.project_id != SKYRISE
    assert (
        match_project_name(
            "Binghatti Skyrise",
            index,
            exclude=frozenset({SKYRISE, AQUARISE, CIRCLE, BUGATTI}),
        )
        is None
    )


def test_the_index_comes_from_inventory_and_nowhere_else(index):
    """No alias table. Invariant 1 allows exactly one source of project
    names, and an alias list is a second one - authored by hand, unvalidated,
    and free to drift from the file the prices come from."""
    assert set(index.names) == {p.id for p in load_inventory()}
    assert set(index.names.values()) == {p.name for p in load_inventory()}


# --- reading the reply -------------------------------------------------------


@pytest.mark.parametrize(
    "reply,agreed,contradicted",
    [
        ("yes", True, False),
        ("that's correct", True, False),
        ("no", False, True),
        ("no, that's wrong", False, True),
        ("not that one", False, True),
        ("I don't think so", False, True),
        # Precedence. Both phrasings carry an agreement word AND a rejection,
        # and reading the agreement first recorded a rejection as consent -
        # the defect that blocked the budget half twice, once per phrasing.
        ("no, that's not right", False, True),
        ("yes, but not that one", False, True),
        # Consent is never inferred from the absence of an objection.
        ("can you repeat that?", False, False),
        ("hmm", False, False),
        ("what does it cost", False, False),
    ],
)
def test_a_reply_is_read_for_agreement_with_contradiction_winning(
    words, reply, agreed, contradicted
):
    reading = read_agreement(reply, words, "en")
    assert (reading.agreed, reading.contradicted) == (agreed, contradicted)


def test_a_language_with_no_authored_words_hears_neither(words):
    """ar/hi word lists are empty until a native reviewer fills them. The safe
    reading of an unheard reply is "no signal", which is a failed attempt -
    never consent."""
    reading = read_agreement("yes", words, "ar")
    assert not reading.agreed
    assert not reading.contradicted
    assert "ar" not in words.languages_covered()


# --- the policy, across turns -------------------------------------------------


def test_a_confident_match_settles_without_asking(index, words):
    p = policy(index, words)
    decision = p.observe("Tell me about Binghatti Skyrise")
    assert not decision.speaks
    assert decision.settled
    assert decision.project_id == SKYRISE
    assert p.confirmed == frozenset({SKYRISE})


def test_a_marginal_match_asks_and_an_explicit_yes_settles_it(index, words):
    p = policy(index, words)
    first = p.observe("Tell me about Binghatti Skyrize")
    assert first.action == "confirm_project"
    assert first.name == "Binghatti Skyrise"

    second = p.observe("Yes, that's right")
    assert not second.speaks
    assert second.settled
    assert second.project_id == SKYRISE


def test_a_settled_project_is_never_read_back_again(index, words):
    """A policy that asks every turn is one the operator switches off."""
    p = policy(index, words)
    p.observe("Tell me about Binghatti Skyrize")
    p.observe("yes")
    assert not p.observe("and what does a studio at Binghatti Skyrize cost").speaks


def test_no_to_a_read_back_asks_which_project_and_never_re_offers_it(
    index, words
):
    p = policy(index, words)
    assert p.observe("Binghatti Skyrize").action == "confirm_project"

    rejected = p.observe("No, that's not it")
    assert rejected.action == "ask_project"

    # The rejected project is out of the running, so the same mangled words
    # cannot resolve back to it.
    again = p.observe("Binghatti Skyrize")
    assert again.project_id != SKYRISE


def test_the_reply_naming_a_different_project_replaces_the_offer(index, words):
    """The stale-mention defect, in its project-name form: settling the name we
    guessed against a reply that named another one."""
    p = policy(index, words)
    assert p.observe("Binghatti Rise").project_id == SKYRISE

    corrected = p.observe("no, the Aquarise")
    assert corrected.settled or corrected.action == "confirm_project"
    assert corrected.project_id == AQUARISE


def test_a_signal_free_reply_is_a_failed_attempt_not_consent(index, words):
    p = policy(index, words)
    p.observe("Binghatti Skyrize")
    second = p.observe("Can you repeat that?")
    assert second.action == "confirm_project"
    assert not second.settled
    assert p.confirmed == frozenset()


def test_three_signal_free_replies_hand_the_buyer_over(index, words):
    p = policy(index, words)
    p.observe("Binghatti Skyrize")
    assert p.observe("Can you repeat that?").action == "confirm_project"
    assert p.observe("Sorry, the line is bad").action == "confirm_project"
    third = p.observe("What was that")
    assert third.action == "project_give_up"
    assert third.hands_over
    assert p.handed_over


def test_a_handed_over_policy_never_speaks_again(index, words):
    p = policy(index, words)
    p.observe("Binghatti Skyrize")
    for _ in range(3):
        p.observe("what")
    assert p.handed_over
    assert not p.observe("Binghatti Aquarize").speaks


def test_the_pending_question_is_readable_without_consuming_an_attempt(
    index, words
):
    """The seam the failed-recognition trigger needs: a turn nobody could hear
    re-asks the open question and counts nothing, because it answered
    nothing."""
    p = policy(index, words)
    p.observe("Binghatti Skyrize")

    for _ in range(5):
        pending = p.pending
        assert pending is not None
        assert pending.action == "confirm_project"
        assert pending.name == "Binghatti Skyrise"

    # Five reads later the buyer still has all three attempts.
    assert p.observe("what").action == "confirm_project"
    assert p.observe("what").action == "confirm_project"
    assert p.observe("what").action == "project_give_up"


def test_abandon_closes_the_policy_rather_than_freeing_the_model(index, words):
    """The fail-closed seam. The caller uses this when it could not SPEAK the
    question; leaving the policy open would hand the turn back to the model
    with the name unconfirmed, which is the fail-open defect the budget half
    shipped."""
    p = policy(index, words)
    p.observe("Binghatti Skyrize")
    p.abandon()
    assert p.handed_over
    assert p.pending is None
    assert not p.observe("Binghatti Skyrize").speaks


def test_a_second_project_later_in_the_call_gets_its_own_question(index, words):
    """Unlike the budget, a settled project does not close the policy: a call
    moves from one tower to another and each marginal mention deserves its own
    read-back."""
    p = policy(index, words)
    p.observe("Binghatti Skyrize")
    assert p.observe("yes").project_id == SKYRISE

    later = p.observe("and the Binghatti Aqua Rise?")
    assert later.action == "confirm_project"
    assert later.project_id == AQUARISE


# --- the two rules this inventory does not yet exercise ----------------------
#
# `_MARGIN` and `_MIN_TOKEN` are both live rules that today's four records
# cannot trigger: no two names are similar enough to need the margin, and no
# area name has a two-letter token in it. A guard nobody exercises is a guard
# nobody has tested, so these build the index from FIXTURE records - never new
# entries in data/inventory.json (AGENTS.md) - and pin the rules against the
# inventory this project will plausibly have next.


def fixture_project(project_id: str, name: str, area: str):
    from ambassador.schemas import Project

    return Project(
        id=project_id,
        name=name,
        area=area,
        status="selling",
        unit_types=["studio"],
        source_ref="fixture",
    )


def test_two_similar_names_are_marginal_however_high_the_score():
    """`_MARGIN`. A developer that ships "Skyrise Tower" and "Skyrise
    Residences" gives the recogniser a one-token difference to lose, and a
    perfect score against one of them is no longer proof of which."""
    index = build_name_index(
        [
            fixture_project("a", "Binghatti Skyrise Tower", "Business Bay"),
            fixture_project("b", "Binghatti Skyrise Towers", "Business Bay"),
        ]
    )
    match = match_project_name("Binghatti Skyrise Tower", index)
    assert match is not None
    assert match.similarity >= 0.95
    assert match.similarity - match.runner_up < 0.12
    assert match.band == "marginal"


def test_a_two_letter_area_token_cannot_suppress_a_real_match():
    """`_MIN_TOKEN`. "Al Jaddaf" is on this project's own pronunciation list,
    so a two-letter decoy token is one inventory edit away. Scored, `al` would
    match most short words in an utterance well enough to clear the floor and
    outweigh a single-token project match - suppressing exactly the mangled
    names the policy exists to catch."""
    index = build_name_index(
        [fixture_project("a", "Binghatti Skyrise", "Al Jaddaf")]
    )
    assert ("al",) not in index.decoys
    assert match_project_name("Bint Jbeil Sky Rise, all of them", index) is not None
