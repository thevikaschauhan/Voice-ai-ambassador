"""The one declinable contact ask (P2-S05, docs/10- 'Contact capture').

Every import is inside its test on purpose: the module does not exist yet, and a
module-level import would fail COLLECTION, which reports an error rather than N
failing cases and leaves the gate nothing to count against the new behaviour.

The load-bearing tests here are the ones about restraint. The policy asks ONCE,
a second goodbye is honoured immediately, a decline is a valid outcome rather
than a failure to retry, and no contact value ever reaches an emitted event.
"""

from __future__ import annotations

import io

import pytest


def test_first_goodbye_asks_once_second_goodbye_closes_and_decline_is_valid() -> None:
    """The card's named test: the whole shape of the interception in one case.

    First farewell gets the contact request INSTEAD of the closing line; the
    second farewell is honoured whatever happened in between; and declining is
    an answer, not an error - it records `declined` and proceeds.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")

    first = policy.on_farewell(turn_index=6)
    assert first is not None, "the first goodbye must be intercepted for the ask"
    assert first.speaks
    assert policy.state.status == "not_asked", "asking is not capturing"
    assert policy.state.asked_turn_index == 6

    # The buyer declines in the reply to that ask.
    outcome = policy.observe_reply("no thanks, I would rather not", turn_index=7)
    assert outcome.settled
    assert policy.state.status == "declined"
    assert policy.state.phone is None and policy.state.email is None

    # And the second goodbye closes immediately - no second ask.
    second = policy.on_farewell(turn_index=8)
    assert second is None, "a second goodbye is honoured, not answered with another ask"


def test_the_policy_owes_one_request_and_only_one() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    assert policy.owes_request()
    policy.on_farewell(turn_index=3)
    assert not policy.owes_request(), "the ask is owed once, whatever comes back"


def test_a_phone_is_read_back_before_it_is_accepted() -> None:
    """One misheard digit is worse than no number (docs/10-)."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    outcome = policy.observe_reply("It is Sara, 050 123 4567", turn_index=5)

    assert not outcome.settled, "a number is not captured until it is confirmed"
    assert outcome.speaks is not None
    assert "0501234567" in outcome.speaks.replace(" ", "")
    assert policy.state.status == "unconfirmed"


def test_the_read_back_is_rendered_from_data_and_never_by_a_model() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=1)
    first = policy.observe_reply("Sara on 050 123 4567", turn_index=2)

    other = ContactPolicy(load_contact_copy(), language="en")
    other.on_farewell(turn_index=1)
    second = other.observe_reply("Sara on 050 123 4567", turn_index=2)

    # Deterministic: the same reply produces the same echo, byte for byte.
    assert first.speaks == second.speaks


def test_a_confirmed_read_back_captures_the_number() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Sara, 050 123 4567", turn_index=5)
    outcome = policy.observe_confirmation("yes that is right", turn_index=6)

    assert outcome.settled
    assert policy.state.status == "captured"
    assert policy.state.phone == "0501234567"
    assert policy.state.name == "Sara"
    assert policy.state.confirmed is True
    assert policy.state.contact_permission is True


def test_a_contradicted_read_back_records_unconfirmed_and_does_not_ask_again() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Sara, 050 123 4567", turn_index=5)
    outcome = policy.observe_confirmation("no, that is wrong", turn_index=6)

    # A failed confirmation proceeds to the farewell; it never asks a second
    # time (docs/10-).
    assert outcome.settled
    assert policy.state.status == "unconfirmed"
    assert policy.state.phone is None
    assert policy.on_farewell(turn_index=7) is None


def test_a_value_absent_from_the_reply_is_refused() -> None:
    """It cannot lift a number from an older property discussion (docs/10-)."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    # The reply names no contact at all. A policy that reached back into the
    # conversation for "985000" would be inventing consent.
    outcome = policy.observe_reply("what about the payment plan", turn_index=5)

    assert policy.state.phone is None
    assert policy.state.email is None
    assert outcome.settled, "an unanswered ask settles rather than repeating"
    assert policy.state.status == "declined"


def test_an_email_must_also_come_from_the_reply() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=2)
    outcome = policy.observe_reply("Sara, sara@example.com", turn_index=3)

    assert outcome.settled, "an email needs no digit echo"
    assert policy.state.status == "captured"
    assert policy.state.email == "sara@example.com"
    assert policy.state.phone is None


def test_the_policy_is_disabled_for_a_language_with_no_authored_copy() -> None:
    """Arabic and Hindi are DISABLED until a native reviewer authors the line."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    copy = load_contact_copy()
    for language in ("ar", "hi"):
        policy = ContactPolicy(copy, language=language)
        assert not policy.owes_request(), f"{language} has no authored ask"
        assert policy.on_farewell(turn_index=3) is None, (
            f"{language} must close on the first goodbye, not ask in English"
        )
        assert policy.state.status == "not_asked"


def test_the_captured_contact_reaches_the_snapshot_the_writer_persists() -> None:
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Sara, 050 123 4567", turn_index=5)
    policy.observe_confirmation("yes", turn_index=6)

    capture = policy.state
    # The shape `adapter/persist.py` already writes, so the contact lands
    # without that layer changing.
    assert capture.status == "captured"
    assert capture.source_turn_index == 5
    assert capture.asked_turn_index == 4


def test_no_contact_value_ever_reaches_an_emitted_event() -> None:
    """The whole point of the redaction rule, asserted over the stream itself."""
    from adapter.events import EventLog
    from ambassador.contact import ContactPolicy, load_contact_copy

    buf = io.StringIO()
    log = EventLog("sess_contact", stream=buf, verbose=False)

    policy = ContactPolicy(load_contact_copy(), language="en", log=log)
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Sara, 050 123 4567 or sara@example.com", turn_index=5)
    policy.observe_confirmation("yes", turn_index=6)

    stream = buf.getvalue()
    for secret in ("0501234567", "050 123 4567", "sara@example.com", "Sara"):
        assert secret not in stream, f"{secret!r} reached the event stream"
    # It still says something happened, or the audit is blind.
    assert "contact" in stream


# --- a greeting is not a name (task-contact-greeting-not-a-name) -----------
#
# Found in Kelly's memo: `_NOT_A_NAME` held no greeting or filler words, so the
# first word of an ordinary polite reply became the buyer's name.


def test_a_reply_that_opens_with_a_greeting_still_finds_the_name() -> None:
    """The four replies from the card, one assertion each.

    Separate cases in one test with the reply in the message, because the rule
    is per-word: a regression should say WHICH opening still swallows the name.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")

    for reply, expected in (
        ("Hi, I am Ahmed, 0501234567", "Ahmed"),
        ("Hello, this is Ahmed", "Ahmed"),
        ("Ahmed speaking", "Ahmed"),
        ("Sure thing, Ahmed", "Ahmed"),
    ):
        assert policy._name_in(reply) == expected, reply


def test_a_reply_that_is_only_greeting_and_filler_yields_no_name() -> None:
    """No name is a valid answer; a filler word standing in for one is not.

    `None` here means the buyer said nothing that could be a name, which the
    policy already handles. "um" as a name is a value somebody would later read.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")

    for reply in ("Hello, um, yeah", "Hi there", "yeah well hmm"):
        assert policy._name_in(reply) is None, reply


def test_the_name_that_reaches_the_vocative_context_is_the_buyer_s() -> None:
    """The end of the chain, and the reason this is not cosmetic.

    `_name_in` feeds `_pending_name`, which `names_given` exposes, which
    `adapter.agent` hands to `VocativeContext.with_names` - the set of names
    the invented-name validator will let the agent SAY. A greeting captured as
    a name does not just sit in a record; it widens that allowlist.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Hi, I am Ahmed, 0501234567", turn_index=5)

    assert policy.names_given == frozenset({"Ahmed"})

    outcome = policy.observe_confirmation("yes that is right", turn_index=6)
    assert outcome.settled
    assert policy.state.name == "Ahmed"
    assert policy.state.phone == "0501234567"


def test_a_plain_name_and_number_is_unchanged() -> None:
    """The regression guard: the reply this policy was written for."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=4)
    policy.observe_reply("Ahmed, 0501234567", turn_index=5)
    policy.observe_confirmation("yes", turn_index=6)

    assert policy.state.name == "Ahmed"
    assert policy.state.phone == "0501234567"


# --- the after-interest ask (task-p2-contact-ask-after-interest) -------------
#
# The human's 05:12Z call is the reason these exist: contact_ask was TRUE,
# contact_line_spoken was 0, and the call ended `buyer_left` after 13 turns. The
# ask only ever fired on a goodbye, and a buyer who hangs up never says one - so
# the one moment the ambassador asks for a way to follow up was unreachable for
# exactly the buyers most worth following up.
#
# WHY THE SIGNAL IS A PAIR OF WORD LISTS AND NOT A MODEL CALL: god's boundary is
# that detection must be deterministic from what the turn pipeline already has.
# A budget is already detected deterministically (`budget.find_budget`, ADR-011),
# so that signal is READ off the confirmation step rather than re-parsed here. A
# timeline and a viewing request had no detector at all, and the two below are
# the smallest honest ones: a time expression only counts as a timeline when the
# buyer put themselves in the sentence, and a viewing only counts when they
# asked for one.
#
# ENGLISH ONLY, and it costs nothing. `ContactCopy.enabled` gates the ask on
# authored copy and only `en` has any, so a trigger that reads English alone can
# never miss an ask that could have been spoken - the same reasoning as
# `_NOT_A_NAME`, without its trade-off.


def test_a_time_expression_is_a_timeline_only_with_the_buyer_in_the_sentence() -> None:
    """ "Is the handover next month?" is a question about the project.

    Both sentences carry the same time expression, and only one of them is the
    buyer saying when they intend to act. Without the first-person half, every
    handover question in the call would spend the one ask.
    """
    from ambassador.contact import interest_signal

    assert interest_signal("I want to buy next month.") == "timeline"
    assert interest_signal("We are looking to move in by December.") == "timeline"
    assert interest_signal("Is the handover next month?") is None
    assert interest_signal("Tell me about the payment plan.") is None


def test_a_callback_request_is_a_signal_and_a_price_question_is_not() -> None:
    from ambassador.contact import interest_signal

    assert interest_signal("Can you call me back tomorrow?") == "callback"
    assert interest_signal("Please get back to me on that.") == "callback"
    assert interest_signal("What is the price of a studio?") is None


def test_a_viewing_needs_both_the_asking_and_the_visiting() -> None:
    """ "I can see the payment plan" is not a request to visit anything."""
    from ambassador.contact import interest_signal

    assert interest_signal("I would like to see the apartment.") == "viewing"
    assert interest_signal("Can I book a viewing this week?") == "viewing"
    assert interest_signal("I can see the payment plan now.") is None


def test_asking_for_a_person_is_not_an_interest_signal() -> None:
    """The one deviation from the card's wording, and it is deliberate.

    The card names "a viewing/callback/human request". A deterministic line
    REPLACES the model's turn, so triggering on "put me through to someone"
    would answer a request to be transferred with a request for a phone number
    and leave `escalate_to_human` uncalled for that turn. A missed ask is the
    status quo; an obstructed hand-over is a new failure, and docs/04- is
    explicit about what making a buyer repeat themselves costs.

    A callback request is still a signal: the ask is a direct answer to it.
    """
    from ambassador.contact import interest_signal

    assert interest_signal("Can I speak to someone about this?") is None
    assert interest_signal("Put me through to a person, please.") is None


def test_the_ask_fires_once_whichever_path_reaches_it_first() -> None:
    """One ask per call, and the two paths share one flag rather than two.

    `owes_request()` is the whole once-only rule, so a goodbye after an
    after-interest ask is honoured immediately - the same answer the second
    goodbye already gets.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.note_interest("budget", turn_index=3)
    assert policy.owes_request(), "noting interest is not asking"

    asked = policy.on_interest(turn_index=4)
    assert asked is not None and asked.speaks
    assert policy.state.asked_turn_index == 4
    assert not policy.owes_request()

    assert policy.on_interest(turn_index=5) is None, "one ask means one ask"
    assert policy.on_farewell(turn_index=9) is None, (
        "a goodbye after an earlier ask is honoured, not intercepted for a second"
    )


def test_nothing_is_asked_until_interest_is_noted() -> None:
    """The inverse, so the trigger is a trigger rather than an unconditional ask.

    A call with no high-intent turn and no goodbye ends unasked, which is the
    behaviour this change must leave alone.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    assert policy.on_interest(turn_index=2) is None
    assert policy.owes_request(), "an unasked call still owes the ask at a goodbye"


def test_a_language_with_no_authored_ask_is_never_asked_after_interest() -> None:
    """AGENTS.md:52 on the farewell path, held on the new one too.

    An Arabic call with a stated budget must not be handed the English
    sentence: this is the one moment the buyer is asked to hand something
    over, and a disabled language means disabled on every path.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    for language in ("ar", "hi"):
        policy = ContactPolicy(load_contact_copy(), language=language)
        policy.note_interest("budget", turn_index=2)
        assert policy.on_interest(turn_index=3) is None
        assert policy.state.status == "not_asked"
        assert policy.state.asked_turn_index is None


def test_the_ask_names_which_trigger_fired_and_the_signals_are_a_closed_set() -> None:
    """`contact_line_spoken`'s stage is how an operator sees which path fires.

    The stage is derived from the signal rather than passed alongside it, so a
    signal the detector can return and the stage cannot name is impossible.
    """
    from ambassador.contact import INTEREST_SIGNALS, ContactPolicy, load_contact_copy

    assert INTEREST_SIGNALS == frozenset({"budget", "timeline", "callback", "viewing"})

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.note_interest("timeline", turn_index=1)
    step = policy.on_interest(turn_index=2)
    assert step is not None
    assert step.stage == "ask_after_timeline"

    farewell = ContactPolicy(load_contact_copy(), language="en").on_farewell(
        turn_index=2
    )
    assert farewell is not None and farewell.stage == "ask"


def test_an_unknown_signal_is_refused_rather_than_emitted() -> None:
    """A typo would otherwise ship a stage value no dashboard can read."""
    import pytest

    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    with pytest.raises(ValueError, match="interest signal"):
        policy.note_interest("vibes", turn_index=1)


def test_the_first_noted_interest_is_the_one_that_is_reported() -> None:
    """Two triggers in one call are one ask, named after the first.

    The stage answers "what made this buyer worth asking", and that is the
    turn interest FIRST appeared - a later callback request does not rewrite
    the budget that opened the door.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.note_interest("budget", turn_index=2)
    policy.note_interest("callback", turn_index=6)
    step = policy.on_interest(turn_index=7)
    assert step is not None and step.stage == "ask_after_budget"


# --- the read-back consent defect (task-contact-agrees-consent-defect) -------
#
# `_agrees` reused the shared word lists but reimplemented the matching, and
# got wrong the two things `projects.read_agreement` gets right: it matched on
# a bare substring instead of a word boundary, and it never read
# `contradictions`, so nothing could win over an affirmation. Every one of
# these was True before the fix, on the live English path.

REFUSALS = [
    # substring of an affirmation inside its own negation
    ("that is not correct", "correct"),
    ("no, that is not correct", "correct"),
    ("absolutely not", "absolutely"),
    ("sorry, incorrect", "correct inside incorrect"),
    ("I am not sure", "sure"),
    # an affirmation inside an unrelated word
    ("I want to book a viewing", "ok inside book"),
    # the precedence `projects.Agreement` documents: a contradiction in the
    # same reply wins, whichever order the buyer says them in
    ("yes, not that one", "contradiction wins over a leading yes"),
    ("no, that's right", "contradiction wins over a trailing right"),
]


@pytest.mark.parametrize("reply,why", REFUSALS)
def test_a_read_back_reply_carrying_a_no_is_never_consent(reply, why) -> None:
    """A wrong number recorded as confirmed is a call to a stranger.

    Asserted through the policy rather than the matcher, because the value at
    risk is the stored one: `confirmed`, `contact_permission` and the phone
    itself, not a boolean in the middle.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=1)
    policy.observe_reply("Sara, 050 123 4567", turn_index=2)

    outcome = policy.observe_confirmation(reply, turn_index=3)

    assert outcome.settled, "settled either way - the policy never re-asks"
    assert policy.state.status == "unconfirmed", why
    assert policy.state.phone is None, "a contradicted number is not kept"
    assert policy.state.confirmed is False
    assert policy.state.contact_permission is False


@pytest.mark.parametrize(
    "reply", ["yes", "yes, that is right", "correct", "yep that's it", "perfect"]
)
def test_a_plain_agreement_still_captures_the_number(reply) -> None:
    """The other direction, or the fix would just be a refusal machine."""
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=1)
    policy.observe_reply("Sara, 050 123 4567", turn_index=2)

    policy.observe_confirmation(reply, turn_index=3)

    assert policy.state.status == "captured"
    assert policy.state.phone == "0501234567"
    assert policy.state.confirmed is True
    assert policy.state.contact_permission is True


def test_an_unauthored_language_cannot_read_consent_and_discards_the_number() -> None:
    """Arabic has no authored yes-words, so the safe direction is the only one.

    Sweeping every language's affirmations at once - what the old matcher did -
    would let an English "yes" settle an Arabic read-back while an Arabic "no"
    went unread, because only the affirmations were being swept. Reading both
    halves from the SAME language is what removes that asymmetry.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="ar")
    policy._pending_phone = "0501234567"

    policy.observe_confirmation("نعم", turn_index=3)

    assert policy.state.status == "unconfirmed"
    assert policy.state.phone is None


def test_a_promoted_language_reads_its_own_yes_and_its_own_no(monkeypatch) -> None:
    """The language argument does its job the day ar/hi words are promoted.

    Driven with a SYNTHETIC vocabulary rather than by promoting the drafts in
    data/currencies.yaml, so this proves the plumbing without shipping copy no
    native speaker has cleared. The words here are only fixtures.

    The second half is the one that matters: "غير صحيح" ("not correct")
    contains the affirmation "صحيح", exactly the shape that made
    "that is not correct" read as consent in English.
    """
    import dataclasses

    from ambassador import budget
    from ambassador.contact import ContactPolicy, load_contact_copy

    fixture = dataclasses.replace(
        budget.load_currency_vocabulary(),
        affirmations={"ar": ("نعم", "صحيح")},
        contradictions={"ar": ("لا", "غير صحيح")},
        negators={"ar": ()},
    )
    monkeypatch.setattr(budget, "load_currency_vocabulary", lambda *a, **k: fixture)

    agreed = ContactPolicy(load_contact_copy(), language="ar")
    agreed._pending_phone = "0501234567"
    agreed.observe_confirmation("نعم", turn_index=3)
    assert agreed.state.status == "captured"
    assert agreed.state.phone == "0501234567"

    refused = ContactPolicy(load_contact_copy(), language="ar")
    refused._pending_phone = "0501234567"
    refused.observe_confirmation("غير صحيح", turn_index=3)
    assert refused.state.status == "unconfirmed", (
        "the affirmation sits inside the contradiction; the contradiction wins"
    )
    assert refused.state.phone is None


def test_a_language_switch_between_the_read_back_and_the_reply_fails_safe() -> None:
    """The adapter cancels a pending REPLY on a switch, not a pending
    CONFIRMATION (`agent.py`: `cancel_pending=self._contact_awaiting_reply`),
    so a number awaiting its read-back survives a mid-call language change.

    Reading agreement in the call's own language is what makes that safe. The
    old sweep would have accepted an English "yes" for a call that had just
    become Arabic; now an unauthored language reads no consent at all and the
    number is discarded. Pinned because the safety here is a consequence of the
    language argument rather than something either module states.
    """
    from ambassador.contact import ContactPolicy, load_contact_copy

    policy = ContactPolicy(load_contact_copy(), language="en")
    policy.on_farewell(turn_index=1)
    policy.observe_reply("Sara, 050 123 4567", turn_index=2)

    policy.set_language("ar", cancel_pending=False, turn_index=3)
    policy.observe_confirmation("yes", turn_index=4)

    assert policy.state.status == "unconfirmed"
    assert policy.state.phone is None
    assert policy.state.contact_permission is False


def test_staged_draft_copy_exists_and_cannot_enable_a_language() -> None:
    """Same contract as `farewells.yaml`, on the file where it matters more.

    `enabled()` reads `ask`, and the ask is the one moment in the call where
    the buyer is asked to hand something over. A draft written straight into
    `ask` would turn that on for a language no reviewer has cleared, so the
    staged copy lives under `draft:` where `ContactCopy` cannot see it.

    Asserting the draft EXISTS matters as much as asserting it is inert: an
    empty `draft:` would pass the inertness half while quietly losing the copy.
    """
    import yaml
    from pathlib import Path

    from ambassador.contact import load_contact_copy

    raw = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "data" / "contact.yaml").read_text(
            encoding="utf-8"
        )
    )
    copy = load_contact_copy()
    for language in ("ar", "hi"):
        assert raw[language]["draft"]["ask"].strip(), f"{language} draft ask is staged"
        assert not copy.ask(language), f"{language} staged copy must stay unread"
        assert copy.enabled(language) is False, f"{language} must stay disabled"
