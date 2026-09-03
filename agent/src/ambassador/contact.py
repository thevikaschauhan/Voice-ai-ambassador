"""The one declinable contact ask (P2-S05, docs/10- 'Contact capture').

Pure core: stdlib, yaml and `schemas` only, so it runs under the core-only gate
with no adapter, no database and no framework in scope.

Three rules shape everything here, and each of them is a restraint rather than
a feature.

**One ask.** The policy owns whether a request is still owed, not the model. A
model that can ask twice will ask twice, and a buyer who has already said no is
the last person to ask again. `owes_request()` goes false the moment the ask is
spoken, whatever comes back.

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
_NOT_A_NAME: Final[frozenset[str]] = frozenset(
    {
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
    """What the policy wants said, if anything."""

    speaks: str


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
        self._pending_phone: str | None = None
        self._pending_name: str | None = None
        self._state = ContactCapture(status="not_asked")

    @property
    def state(self) -> ContactCapture:
        return self._state

    def owes_request(self) -> bool:
        """One ask, and only where there is a line to say it in."""
        return not self._asked and self._copy.enabled(self._language)

    def on_farewell(self, turn_index: int) -> ContactStep | None:
        """The first goodbye is intercepted for the ask; a second is honoured.

        Returning None means "let the farewell happen", which is what a second
        goodbye, a disabled language and an already-settled contact all get.
        """
        if not self.owes_request():
            return None
        self._asked = True
        self._state = self._state.model_copy(update={"asked_turn_index": turn_index})
        self._emit("contact_asked", turn=turn_index)
        return ContactStep(speaks=self._copy.ask(self._language))

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
        stranger. The name is also never read back, so it costs the buyer
        nothing to correct on the next call.
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
