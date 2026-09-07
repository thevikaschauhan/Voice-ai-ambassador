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
