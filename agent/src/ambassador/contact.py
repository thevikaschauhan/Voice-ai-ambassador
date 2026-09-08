"""The one declinable contact ask (P2-S05, docs/10- 'Contact capture').

Pure core: stdlib, yaml and `schemas` only, so it runs under the core-only gate
with no adapter, no database and no framework in scope.

Three rules shape everything here, and each of them is a restraint rather than
a feature.

**One ask.** The policy owns whether a request is still owed, not the model. A
model that can ask twice will ask twice, and a buyer who has already said no is
the last person to ask again. `owes_request()` goes false the moment the ask is
spoken, whatever comes back - and it is the SINGLE flag both triggers consult,
which is what makes "one ask per call whichever path fires first" structural
rather than a rule two call sites have to remember.

**Two triggers, one ask.** `on_interest` fires after the first high-intent buyer
turn and `on_farewell` intercepts the first goodbye if nothing has yet. The
second exists because the first cannot cover every call; the FIRST exists
because the second could not cover the calls that matter. A buyer who hangs up
never says goodbye, and the human's 05:12Z call ended `buyer_left` with
`contact_ask` true and `contact_line_spoken` zero.

**The reply is the only source.** A number may only be captured from the reply
to the ask. Reaching back into an earlier property discussion for something
that looks like a phone number would be inventing consent - the buyer said it
about a listing, not about being called.

**A decline is an answer.** `declined` is a settled outcome, not a failure to
retry, and so is `unconfirmed`. Both proceed to the authored farewell.

A language with no authored `ask` is DISABLED rather than defaulted to English:
this is the one moment the ambassador asks the buyer to hand something over,
and doing that in the wrong language reads as a script rather than a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

import yaml

from .schemas import ContactCapture

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# A phone as a buyer says it: digits in groups, optionally with a country code.
# Deliberately narrow - it runs only over the reply to the ask, so it does not
# need to survive a whole conversation, and a loose pattern there would capture
# a price.
_PHONE = re.compile(r"(?:\+?\d[\d\s\-().]{7,17}\d)")

_EMAIL = re.compile(r"[^\s@]+@[^\s@.]+\.[^\s@]+")

_DIGITS = re.compile(r"\d")

# Words that open a sentence without being a name. "It is Sara" and "My name is
# Sara" both hand over one name, and the leading word is not it.
#
# ENGLISH ONLY, and deliberately. Arabic and Hindi greetings and honorifics need
# a native reviewer before they can be called not-names (AGENTS.md:52), and a
# guessed list there would refuse a real buyer their own name in their own
# language - a worse failure than the one this set exists to prevent. The
# greeting group below was added after a reply of the shape "Hi, I am Ahmed,
# 050..." recorded the name "Hi": the words a polite answer opens with are the
# words most likely to arrive in front of the name.
_NOT_A_NAME: Final[frozenset[str]] = frozenset(
    {
        # greetings and acknowledgements
        "hi",
        "hiya",
        "hello",
        "hey",
        "yeah",
        "yep",
        "yup",
        "yo",
        # fillers and hedges
        "well",
        "so",
        "oh",
        "um",
        "uh",
        "hmm",
        "actually",
        "right",
        "please",
        "speaking",
        "here",
        "there",
        # "sure" is already below; "sure thing" needs both words
        "thing",
        "it",
        "its",
        "it's",
        "i",
        "i'm",
        "im",
        "my",
        "me",
        "this",
        "that",
        "the",
        "is",
        "am",
        "name",
        "sure",
        "yes",
        "no",
        "ok",
        "okay",
        "thanks",
        "thank",
        "you",
        "call",
        "email",
        "number",
        "on",
        "at",
        "and",
        "or",
    }
)

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# The shortest run of digits this will treat as a phone number. Below this it is
# a flat number, a floor or a year, and asking the buyer to confirm it would be
# worse than not hearing one.
_MIN_PHONE_DIGITS: Final = 9


# The high-intent signals, and the closed set the stage names are built from.
# A signal the detector can return and the stage cannot name is impossible
# because both read this.
INTEREST_SIGNALS: Final[frozenset[str]] = frozenset(
    {"budget", "timeline", "callback", "viewing"}
)

# ENGLISH ONLY, and unlike `_NOT_A_NAME` this one costs nothing. `enabled()`
# gates the ask on authored copy and only `en` has any, so there is no call
# where a signal this detector cannot read would have been followed by an ask.
# When an Arabic or Hindi ask is native-reviewed, these lists need the same
# reviewer - not a translation.
#
# A BUDGET IS NOT DETECTED HERE. `budget.find_budget` already does it, under
# ADR-011, with a reviewed currency vocabulary and clause analysis this could
# not honestly reproduce; the adapter reads the result off the confirmation
# step it already produced and passes "budget" to `note_interest`. A second
# list is a second architecture for the same job, and the one that goes stale
# is the one nobody is looking at.

# Whoever is doing the wanting. A time expression alone is not a timeline: "Is
# the handover next month?" is a question about the building, and without this
# half every handover question in the call would spend the one ask.
_FIRST_PERSON: Final[frozenset[str]] = frozenset(
    {"i", "im", "id", "ive", "ill", "me", "my", "mine", "we", "us", "our", "ours"}
)

_MONTHS: Final[tuple[str, ...]] = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

# When they intend to act. Deliberately concrete: a bare "soon" or "later" is
# not a timeline anybody could follow up on.
_TIME_PHRASES: Final[tuple[str, ...]] = (
    "next week",
    "next month",
    "next year",
    "this week",
    "this month",
    "this year",
    "end of the year",
    "end of this year",
    "as soon as possible",
    "right away",
    "straight away",
    "immediately",
    "asap",
    "today",
    "tomorrow",
    "this weekend",
    "q1",
    "q2",
    "q3",
    "q4",
    # "by December" is a deadline. A BARE MONTH IS NOT: "the December handover"
    # is a fact about the building, and "tell me about the December handover"
    # would otherwise be a timeline because it contains "me".
    *(f"by {month}" for month in _MONTHS),
)

# Asking for one. These are first-person by construction, which is why they
# need no pronoun test of their own.
#
# A REQUEST TO BE TRANSFERRED IS NOT HERE, and the omission is the point. A
# deterministic line REPLACES the model's turn, so triggering on "put me
# through to someone" would answer a request for a person with a request for a
# phone number and leave `escalate_to_human` uncalled for that turn. A missed
# ask is the status quo; an obstructed hand-over is a new failure (docs/04- on
# what making a buyer repeat themselves costs). A callback request stays,
# because the ask is a direct answer to it.
_CALLBACK_PHRASES: Final[tuple[str, ...]] = (
    "call me",
    "call me back",
    "give me a call",
    "ring me",
    "get back to me",
    "contact me",
    "reach me on",
    "follow up with me",
)

# Wanting something, and the something being a visit. Both halves are required
# for the same reason the timeline needs a pronoun: "I can see the payment
# plan" is not a request to visit anything.
_WANT_PHRASES: Final[tuple[str, ...]] = (
    "i want",
    "i would like",
    "i'd like",
    "id like",
    "we want",
    "we would like",
    "we'd like",
    "can i",
    "could i",
    "can we",
    "could we",
    "let me",
    "i'd love",
    "book",
    "arrange",
    "schedule",
)

_VISIT_PHRASES: Final[tuple[str, ...]] = (
    "viewing",
    "site visit",
    "visit",
    "see the",
    "see it",
    "view the",
    "look around",
    "tour",
    "come by",
    "come and see",
)


def interest_signal(text: str) -> str | None:
    """Which high-intent signal this buyer turn carries, if any.

    Pure, deterministic and cheap: it runs on every buyer turn, so it does no
    model call and no I/O. Returns a member of `INTEREST_SIGNALS` or None.

    ORDERED, and the order is a judgement. An explicit request - a callback, a
    viewing - beats an inferred timeline, because the explicit one says what the
    buyer wants done and the stage on `contact_line_spoken` is how an operator
    finds out which trigger is worth keeping. "Can you call me back tomorrow?"
    is a callback, not a timeline.

    `budget` is never returned here; see the note above `_FIRST_PERSON`.
    """
    lowered = text.lower()
    words = {word for word in _WORD.findall(lowered)}

    if _says_any(lowered, _CALLBACK_PHRASES):
        return "callback"
    if _says_any(lowered, _WANT_PHRASES) and _says_any(lowered, _VISIT_PHRASES):
        return "viewing"
    if words & _FIRST_PERSON and _says_any(lowered, _TIME_PHRASES):
        return "timeline"
    return None


def _says_any(lowered: str, phrases: tuple[str, ...]) -> bool:
    """Whole words only, so "visit" does not match inside another word."""
    return any(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered) for phrase in phrases
    )


class _Log(Protocol):
    """Just enough of `adapter.events.EventLog` to emit, without importing it."""

    def emit(self, event: str, **fields: Any) -> Any: ...


@dataclass(frozen=True)
class ContactCopy:
    """The authored lines, per language. Empty `ask` means DISABLED."""

    lines: dict[str, dict[str, Any]] = field(default_factory=dict)

    def enabled(self, language: str) -> bool:
        return self.ask(language) != ""

    def ask(self, language: str) -> str:
        return str(self.lines.get(language, {}).get("ask", "") or "").strip()

    def confirm_phone(self, language: str) -> str:
        return str(self.lines.get(language, {}).get("confirm_phone", "") or "").strip()

    def correction_failed(self, language: str) -> str:
        return str(
            self.lines.get(language, {}).get("correction_failed", "") or ""
        ).strip()

    def thanks(self, language: str) -> str:
        return str(self.lines.get(language, {}).get("thanks", "") or "").strip()

    def digit_forms(self, language: str) -> dict[str, str]:
        forms = self.lines.get(language, {}).get("digit_forms") or {}
        return {str(key): str(value) for key, value in forms.items()}


def load_contact_copy(path: Path | None = None) -> ContactCopy:
    source = path or _DATA_DIR / "contact.yaml"
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return ContactCopy(
        lines={
            language: block
            for language, block in loaded.items()
            if isinstance(block, dict)
        }
    )


@dataclass(frozen=True)
class ContactStep:
    """What the policy wants said, and which trigger asked for it.

    `stage` travels with the line rather than beside it: it reaches the event
    stream as `contact_line_spoken`'s stage, and a caller that had to choose
    the label itself is a caller that can label the farewell ask as a budget
    one.
    """

    speaks: str
    stage: str


@dataclass(frozen=True)
class ContactOutcome:
    """Whether the contact question is finished, and what to say meanwhile.

    `settled` is the word that matters: it is true for captured, declined AND
    unconfirmed, because all three mean the ask is over and the farewell may
    take the turn. Only a pending read-back leaves it false.
    """

    settled: bool
    speaks: str | None = None


class ContactPolicy:
    """Deterministic, one-shot, and disabled without authored copy."""

    def __init__(
        self,
        copy: ContactCopy,
        language: str,
        log: _Log | None = None,
    ) -> None:
        self._copy = copy
        self._language = language
        self._log = log
        self._asked = False
        self._interest: str | None = None
        self._pending_phone: str | None = None
        self._pending_name: str | None = None
        self._state = ContactCapture(status="not_asked")

    @property
    def state(self) -> ContactCapture:
        return self._state

    @property
    def names_given(self) -> frozenset[str]:
        """Every name the buyer has actually said, settled or not.

        A pending name counts. It becomes pending the moment the buyer says it,
        and the read-back that is still outstanding is about the NUMBER - so
        treating the name as unknown until the digits are confirmed would let
        the invented-name validator (docs/03- validator 5) refuse the buyer
        their own name for a turn.
        """
        return frozenset(
            name for name in (self._state.name, self._pending_name) if name
        )

    def owes_request(self) -> bool:
        """One ask, and only where there is a line to say it in."""
        return not self._asked and self._copy.enabled(self._language)

    def note_interest(self, signal: str, turn_index: int) -> None:
        """Remember the FIRST high-intent turn. Idempotent, and not an ask.

        Separate from `on_interest` because the two happen at different points
        in the turn: interest has to be recorded even when a deterministic
        policy takes the turn - a stated budget ALWAYS opens a currency
        confirmation - or the signal that matters most would never fire.

        The first signal is the one kept. The stage answers "what made this
        buyer worth asking", and that is where interest first appeared; a later
        callback does not rewrite the budget that opened the door.
        """
        if signal not in INTEREST_SIGNALS:
            raise ValueError(
                f"{signal!r} is not an interest signal. A stage nothing can "
                f"read is worse than no stage; the set is {sorted(INTEREST_SIGNALS)}."
            )
        if self._interest is None and self.owes_request():
            self._interest = signal

    def on_interest(self, turn_index: int) -> ContactStep | None:
        """The ask, once a high-intent turn has been seen.

        None until `note_interest` has recorded one, and None forever after the
        ask is spent - the same `owes_request()` the farewell path consults, so
        the two cannot both ask.
        """
        if self._interest is None:
            return None
        return self._ask(turn_index, stage=f"ask_after_{self._interest}")

    def on_farewell(self, turn_index: int) -> ContactStep | None:
        """The first goodbye is intercepted for the ask; a second is honoured.

        Returning None means "let the farewell happen", which is what a second
        goodbye, an ask already spent after interest, a disabled language and an
        already-settled contact all get.
        """
        return self._ask(turn_index, stage="ask")

    def _ask(self, turn_index: int, *, stage: str) -> ContactStep | None:
        """The one ask, whichever trigger reached it.

        Both entry points come through here so the once-only rule, the recorded
        turn index and the `contact_asked` event are one piece of code rather
        than two that have to agree. Only the stage differs, which is what
        `stage` is for.
        """
        if not self.owes_request():
            return None
        self._asked = True
        self._state = self._state.model_copy(update={"asked_turn_index": turn_index})
        self._emit("contact_asked", turn=turn_index)
        return ContactStep(speaks=self._copy.ask(self._language), stage=stage)

    def observe_reply(self, text: str, turn_index: int) -> ContactOutcome:
        """The one reply eligible for extraction.

        A phone goes to a read-back before it is accepted; an email needs none,
        because a misheard address fails visibly and a misheard digit does not.
        Nothing found at all settles as `declined`: the buyer was asked, and
        asking again is the thing this policy exists to prevent.
        """
        phone = self._phone_in(text)
        email = _EMAIL.search(text)
        name = self._name_in(text)

        if phone is not None:
            self._pending_phone = phone
            self._pending_name = name
            self._state = self._state.model_copy(
                update={"status": "unconfirmed", "source_turn_index": turn_index}
            )
            self._emit("contact_read_back", turn=turn_index)
            return ContactOutcome(settled=False, speaks=self._read_back(phone))

        if email is not None:
            self._state = self._state.model_copy(
                update={
                    "status": "captured",
                    "source_turn_index": turn_index,
                    "name": name,
                    "email": email.group(0),
                    "contact_permission": True,
                    "confirmed": True,
                }
            )
            self._emit("contact_settled", turn=turn_index, status="captured")
            return ContactOutcome(
                settled=True, speaks=self._copy.thanks(self._language)
            )

        self._state = self._state.model_copy(
            update={"status": "declined", "source_turn_index": turn_index}
        )
        self._emit("contact_settled", turn=turn_index, status="declined")
        return ContactOutcome(settled=True, speaks=self._copy.thanks(self._language))

    def observe_confirmation(self, text: str, turn_index: int) -> ContactOutcome:
        """Yes accepts the number; anything else records `unconfirmed`.

        It does NOT re-ask, and it does not keep the number: a value the buyer
        has just contradicted is worse than no value, because somebody would
        call it.
        """
        if self._pending_phone is None:
            return ContactOutcome(settled=True)

        if _agrees(text):
            phone, name = self._pending_phone, self._pending_name
            self._pending_phone = self._pending_name = None
            self._state = self._state.model_copy(
                update={
                    "status": "captured",
                    "name": name,
                    "phone": phone,
                    "contact_permission": True,
                    "confirmed": True,
                }
            )
            self._emit("contact_settled", turn=turn_index, status="captured")
            return ContactOutcome(
                settled=True, speaks=self._copy.thanks(self._language)
            )

        self._pending_phone = self._pending_name = None
        self._state = self._state.model_copy(
            update={"status": "unconfirmed", "phone": None, "confirmed": False}
        )
        self._emit("contact_settled", turn=turn_index, status="unconfirmed")
        return ContactOutcome(
            settled=True, speaks=self._copy.correction_failed(self._language)
        )

    def _phone_in(self, text: str) -> str | None:
        for match in _PHONE.finditer(text):
            digits = "".join(_DIGITS.findall(match.group(0)))
            if len(digits) >= _MIN_PHONE_DIGITS:
                return digits
        return None

    def _name_in(self, text: str) -> str | None:
        """The first word in the reply that could be a name.

        Deliberately simple and deliberately not a model: a wrong name is
        embarrassing and recoverable, while a wrong number is a call to a
        stranger. It is not echoed in the read-back, which is about the digits -
        but it does not stop here either: `names_given` hands it to
        `VocativeContext.with_names`, so a word captured here is a word the
        invented-name validator will let the agent SAY to the buyer. That is why
        the filler words are in `_NOT_A_NAME` rather than tolerated as a
        cosmetic blemish on a record.
        """
        without_contacts = _EMAIL.sub(" ", _PHONE.sub(" ", text))
        for word in _WORD.findall(without_contacts):
            if word.lower() in _NOT_A_NAME or len(word) < 2:
                continue
            return word
        return None

    def _read_back(self, digits: str) -> str:
        """The echo, rendered from data and never by a model.

        With no authored digit forms the digits are spoken as digits, which is
        the same fail-towards-digits posture `verbalise.py` takes for money: a
        voice reads them acceptably, and a guessed word for a digit is how a
        number changes on its way back to the buyer.
        """
        forms = self._copy.digit_forms(self._language)
        spoken = " ".join(forms.get(digit, digit) for digit in digits)
        template = self._copy.confirm_phone(self._language)
        return template.replace("{digits}", spoken) if template else spoken

    def _emit(self, event: str, **fields: Any) -> None:
        """Status and turn only.

        The values are the whole reason contact capture is sensitive: a phone
        number in a redacted event stream is a phone number in every log sink
        that stream reaches. This emits that a step HAPPENED and never what was
        said (docs/10- data handling).
        """
        if self._log is not None:
            self._log.emit(event, **fields)


def _agrees(text: str) -> bool:
    """Yes, in the words the budget policy's reviewer already authored.

    Imported lazily from `projects`/`budget`'s shared list rather than a second
    copy here: a second list is a second architecture for the same job, and the
    one that goes stale is the one nobody is looking at (`projects.py`).
    """
    from .budget import load_currency_vocabulary
    from .projects import agreement_words

    words = agreement_words(load_currency_vocabulary())
    lowered = text.lower()
    tokens = set(_WORD.findall(lowered))
    for language_words in words.affirmations.values():
        for word in language_words:
            if not word:
                continue
            if word in tokens or word in lowered:
                return True
    return False
