"""Which ADR-011 policy owns which reply (ambassador/confirmation.py).

The adapter tests in test_agent.py assert what the BUYER hears, which is the
claim that matters. These assert the rule underneath directly, because the
rule is now shared with the eval harness and because two of the three defects
it exists to fix were only visible three turns into an exchange.
"""

from __future__ import annotations

import pytest

from ambassador.budget import BudgetPolicy, load_currency_vocabulary
from ambassador.confirmation import ConfirmationCoordinator
from ambassador.inventory import load_inventory
from ambassador.projects import (
    ProjectNamePolicy,
    agreement_words,
    build_name_index,
)
from ambassador.recognition import RecognitionMonitor, load_noise_words

SKYRISE = "binghatti-skyrise"


@pytest.fixture(scope="module")
def parts():
    vocabulary = load_currency_vocabulary()
    return (
        vocabulary,
        build_name_index(load_inventory()),
        agreement_words(vocabulary),
        load_noise_words(),
    )


def coordinator(parts, *, recognition: bool = True) -> ConfirmationCoordinator:
    vocabulary, index, words, noise = parts
    return ConfirmationCoordinator(
        budget=BudgetPolicy(vocabulary, "en"),
        project=ProjectNamePolicy(index, words, "en"),
        recognition=RecognitionMonitor(noise, "en"),
        budget_runs=True,
        project_runs=True,
        recognition_runs=recognition,
    )


def said(policies: ConfirmationCoordinator, utterance: str):
    """The step that speaks on this turn, or None."""
    return next(
        (step for step in policies.observe(utterance) if step.speaks), None
    )


# --- ownership ---------------------------------------------------------------


def test_an_answer_is_not_discarded_by_the_other_policy_s_precedence(parts):
    """Meredith's exchange, on the state machine. The reply carries an answer
    to the open project question AND a new budget: both are honoured, in that
    order, because precedence is for fresh mentions and never for answers."""
    policies = coordinator(parts)
    assert said(policies, "Binghatti Skyrize").action == "confirm_project"

    step = said(policies, "Yes, and my budget is 2 crore.")
    assert step is not None
    assert step.policy == "budget"
    assert step.action == "ask_currency"
    # The Yes was read by the question it answered.
    assert policies._project.confirmed == frozenset({SKYRISE})


def test_a_reply_to_one_question_never_costs_the_other_an_attempt(parts):
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    # No agreement word, so this answers nothing about the NAME - but it does
    # carry a budget, and a lost budget mention is the twenty-times risk.
    step = said(policies, "It is about 2 crore.")
    assert step is not None and step.policy == "budget"
    assert policies._project._attempts == 0

    # The currency answer belongs to the budget, and the suspended project
    # question is asked again rather than being answered by it.
    step = said(policies, "Dirhams.")
    assert step is not None
    assert step.policy == "project"
    assert step.reask
    assert policies._project._attempts == 0


def test_the_most_recently_asked_question_owns_the_reply(parts):
    """A person takes an answer to be about the last thing they asked. So does
    this: the budget asked second, so "no" rejects the AMOUNT read-back rather
    than the project name."""
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    said(policies, "It is 5 million dirhams.")
    step = said(policies, "No, that is wrong.")
    assert step is not None
    assert step.policy == "budget"
    assert step.action == "ask_amount"


def test_a_reply_nobody_claims_is_a_failed_attempt_on_the_owner(parts):
    """Consent is never inferred, and neither is irrelevance: a reply that
    answers nothing still counts against the question it was given to."""
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    for _ in range(2):
        step = said(policies, "Can you repeat that?")
        assert step is not None and step.action == "confirm_project"
        assert not step.reask
    step = said(policies, "Sorry, what was that?")
    assert step is not None
    assert step.action == "project_give_up"


def test_an_unheard_turn_re_asks_and_counts_nothing(parts):
    policies = coordinator(parts)
    said(policies, "My budget is 2 crore.")
    for _ in range(2):
        step = said(policies, "")
        assert step is not None
        assert step.policy == "budget"
        assert step.action == "ask_currency"
        assert step.reask
    assert policies._budget._attempts == 0


def test_a_suspended_question_is_still_owed(parts):
    """The property ADR-011 exists to hold: the model never takes a turn while
    a confirmation is open, including one that was waiting its turn."""
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    said(policies, "It is about 2 crore.")
    # The budget settles on this turn without speaking; the project question
    # is what is left owed, so it is what the buyer hears.
    steps = policies.observe("Dirhams.")
    assert [step.policy for step in steps] == ["recognition", "budget", "project"]
    assert steps[-1].speaks


# --- quiescence --------------------------------------------------------------


def test_a_recognition_handover_closes_every_policy(parts):
    policies = coordinator(parts)
    said(policies, "My budget is 2 crore.")
    for _ in range(2):
        said(policies, "")
    step = said(policies, "")
    assert step is not None and step.policy == "recognition"
    assert policies.quiesced
    assert policies.observe("") == ()
    assert policies.observe("Dirhams.") == ()
    assert policies.observe("Binghatti Skyrize") == ()


def test_a_project_handover_closes_the_budget_question(parts):
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    said(policies, "It is about 2 crore.")
    # Three replies that answer neither question: the owner gives up, and the
    # other question goes with it.
    for _ in range(2):
        said(policies, "What?")
    step = said(policies, "Pardon?")
    assert step is not None and step.hands_over
    assert policies.quiesced
    assert policies.observe("Dirhams.") == ()


def test_quiescing_abandons_rather_than_flagging(parts):
    """By construction, not by a guard at each read site: a guard is one site
    away from being forgotten, and forgetting one was finding 3."""
    policies = coordinator(parts)
    said(policies, "My budget is 2 crore.")
    policies.quiesce()
    assert policies._budget.settled
    assert policies._project.handed_over
    assert policies._budget.pending is None
    assert policies._project.pending is None


# --- the policies that are switched off --------------------------------------


def test_a_language_without_recognition_copy_still_runs_the_others(parts):
    """Each policy's copy group is independent, so one being unauthored must
    not silence the rest."""
    policies = coordinator(parts, recognition=False)
    step = said(policies, "My budget is 2 crore.")
    assert step is not None and step.policy == "budget"

    # And with that trigger off, an empty turn is a reply that answered
    # nothing - a FAILED attempt, re-asked and counted. That is the behaviour
    # from before the trigger existed, and having it side by side with the
    # covered case is what makes the trigger's value legible: with the copy
    # authored, three unheard turns cost the buyer nothing and bring in a
    # person; without it, they burn the budget policy's three attempts.
    step = said(policies, "")
    assert step is not None
    assert step.policy == "budget"
    assert not step.reask
    assert policies._budget._attempts == 1


def test_the_recognition_trigger_spends_no_attempt_where_it_does_run(parts):
    """The same three turns, with the copy authored. The contrast is the
    point."""
    policies = coordinator(parts, recognition=True)
    said(policies, "My budget is 2 crore.")
    said(policies, "")
    assert policies._budget._attempts == 0


def test_a_reply_about_something_else_does_not_spend_the_open_question(parts):
    """`answers()` earning its keep on the budget side.

    The buyer changes the subject while a currency question is open. That reply
    is not a wrong answer, it is not an answer - so the name it DOES carry gets
    its own question, the currency question is suspended rather than charged an
    attempt, and it comes back afterwards.
    """
    policies = coordinator(parts)
    step = said(policies, "My budget is 2 crore.")
    assert step is not None and step.action == "ask_currency"

    step = said(policies, "Actually, tell me about Binghatti Skyrize.")
    assert step is not None
    assert step.policy == "project"
    assert policies._budget._attempts == 0

    step = said(policies, "Yes, that is right.")
    assert step is not None
    assert step.policy == "budget"
    assert step.reask
    assert policies._budget._attempts == 0


def test_ownership_is_recency_and_not_precedence(parts):
    """The rule stated in this module, asserted as the rule.

    With exactly two questionable policies this ordering is not reachable
    through `observe`: the project question can only open while the budget is
    quiet, so whenever both are open the budget is the newer of the two and
    recency and precedence agree. The state is therefore set directly - the
    test exists so that the rule cannot quietly decay into "budget first",
    which is what it would become the moment a third question, or a reopening
    one, made the difference visible.
    """
    policies = coordinator(parts)
    said(policies, "Binghatti Skyrize")
    said(policies, "It is about 2 crore.")
    assert policies._budget.pending is not None
    assert policies._project.pending is not None

    assert policies._owner() == "budget"
    policies._last_asked = "project"
    assert policies._owner() == "project"
