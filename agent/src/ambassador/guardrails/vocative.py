"""Invented-name validator: never call the buyer a name they did not give.

docs/03- validator 5, added after the 08:32Z call said "You are welcome, Jim."
to a buyer who never gave a name. The numeric validator inspects figures and
the prohibited-pattern validator inspects claims; a name is neither, so the
sentence went to TTS unexamined.

## Positional, not a name list

There is no list of personal names worth having - the model can invent any
word, so a list catches only the inventions somebody already thought of. What
IS checkable is the shape of direct address:

  1. a comma-vocative at either end - "You are welcome, Jim." / "Jim, the
     payment plan runs over three years."
  2. a greeting or thanks with no comma - "Hi Jim", "Thanks Jim"

and then the small set of names that are LEGITIMATE in that slot: the ones the
buyer gave (from the contact capture), the ambassador's own, the inventory's
vocabulary, and forms of address.

## The one place a list is the right tool

English puts a sentence adverb in exactly the slot a leading vocative uses -
"Actually, the booking amount is 20 per cent." is not an address to somebody
called Actually. Running this validator over every recorded model reply in
`evals/cases` flagged two sentences in 102, both that shape ("Actually," and
"Noted,"), which is what `sentence_openers` is for. A list works there and
would not work for names because sentence openers are a CLOSED class of
function words and personal names are an open one.

## The licence, which is what keeps it shippable

A comma-vocative fires only in a sentence that is addressing someone - one that
carries a second-person pronoun or an address word. Without that rule "It is in
Business Bay, Dubai." reads as an address to somebody called Dubai, and refusing
that sentence would refuse most of the product. The live sentence carries "You",
which is how it was caught.

## Which direction the errors go

A violation costs a REGENERATION, not a call: the sentence is refused, the model
is asked again with the violation named, and the composed fallback stands behind
that (docs/01-). That asymmetry is the opposite of the farewell detector's,
where a false positive hangs up on a live buyer - which is why this validator
can be strict and that one cannot.

## English only

The tables are per-language and ar/hi are EMPTY until a native reviewer writes
them (AGENTS.md:52). Empty is safe in the direction that matters: with no
address vocabulary the validator never fires, and the sentence goes through as
it does today. A machine-guessed Arabic vocative rule would refuse real Arabic
sentences instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..ambassadors import load_ambassadors
from ..inventory import load_inventory, vocabulary

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"

# A capitalised word that could be a name: initial upper-case letter in any
# script, then letters only. Digits and punctuation cannot be a name, and an
# ALL-CAPS token is an acronym rather than an address.
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class VocativeMarkers:
    """The address vocabulary, per language."""

    address_words: dict[str, frozenset[str]]
    second_person: dict[str, frozenset[str]]
    forms_of_address: dict[str, frozenset[str]]
    sentence_openers: dict[str, frozenset[str]]

    def detects(self, language: str) -> bool:
        """Whether direct address can be recognised in this language at all.

        False is a supported state: with no authored vocabulary the validator
        does not fire and the sentence is spoken exactly as it is today.
        """
        return bool(self.address_words.get(language)) or bool(
            self.second_person.get(language)
        )


@dataclass(frozen=True)
class VocativeContext:
    """What a call knows: the names that are legitimate, and the tables.

    `known` is every name this sentence may use - the buyer's own from the
    contact capture, the ambassador's, and the inventory's vocabulary. Empty is
    the normal state of a call before the buyer has given anything, which is
    exactly the state the 08:32Z call was in.
    """

    known: frozenset[str]
    markers: VocativeMarkers

    def with_names(self, names: frozenset[str]) -> "VocativeContext":
        """The same tables, plus names this call has since learned.

        Returns self when there is nothing to add, which is every sentence of
        every call until the buyer answers the contact ask - so the common path
        allocates nothing.
        """
        if not names:
            return self
        return VocativeContext(known=self.known | names, markers=self.markers)


def load_vocative_markers(path: Path | None = None) -> VocativeMarkers:
    source = path or _DATA_DIR / "vocatives.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping, got {type(raw).__name__}."
        )

    def section(name: str) -> dict[str, frozenset[str]]:
        out: dict[str, frozenset[str]] = {}
        for language, values in (raw.get(name) or {}).items():
            words: set[str] = set()
            for value in values or []:
                if not isinstance(value, str):
                    # The YAML 1.1 boolean trap: bare no/yes load as booleans,
                    # and a coerced boolean is a word that can never match.
                    raise ValueError(
                        f"{source.name}: {name}.{language} contains {value!r}, "
                        "not text. Quote the entry in the data file."
                    )
                words.update(token.lower() for token in _TOKEN.findall(value))
            out[language] = frozenset(words)
        return out

    return VocativeMarkers(
        address_words=section("address_words"),
        second_person=section("second_person"),
        forms_of_address=section("forms_of_address"),
        sentence_openers=section("sentence_openers"),
    )


def load_vocative_context(known: frozenset[str] = frozenset()) -> VocativeContext:
    """The context a call starts in: our own words, and whatever the buyer gave.

    `known` is the buyer's side and defaults to EMPTY, which is the state every
    call is in until the buyer answers the contact ask - and the state the
    08:32Z call was in when it said "You are welcome, Jim." Our side is the
    inventory's vocabulary and the ambassadors' own names, both of which land
    in a vocative slot legitimately and neither of which anybody invented.

    Assembled here rather than at each composition root because there are three
    of them - the voice adapter, the eval harness and the tests - and a
    validator that is strict in one and lax in another is worse than either.
    """
    return VocativeContext(
        known=known | vocabulary(load_inventory()) | _ambassador_names(),
        markers=load_vocative_markers(),
    )


def _ambassador_names() -> frozenset[str]:
    """Every ambassador's given name, in every language.

    Every language's, not the call's: the names are spoken and written, a buyer
    can repeat one back, and a name that is ours in one language is not an
    invention in another.
    """
    return frozenset(name for name in load_ambassadors().names.values() if name)


def _addresses_someone(
    tokens: list[str], markers: VocativeMarkers, language: str
) -> bool:
    """Is this sentence spoken TO the buyer rather than about a property?"""
    lowered = {token.lower() for token in tokens}
    return bool(lowered & (markers.second_person.get(language) or frozenset())) or bool(
        lowered & (markers.address_words.get(language) or frozenset())
    )


def _candidate(token: str, allowed: frozenset[str]) -> bool:
    """Could this token be a personal name nobody gave us?

    Capitalised and not ALL-CAPS: `AED` and `SPA` are acronyms, and an acronym
    in a vocative slot is a transcription artefact rather than a name.
    """
    if not token[:1].isupper() or token.isupper():
        return False
    return token.lower() not in allowed


def check_invented_vocative(
    sentence: str,
    language: str,
    *,
    known: frozenset[str],
    markers: VocativeMarkers,
) -> str | None:
    """The name this sentence calls the buyer, if the buyer never gave it."""
    if not markers.detects(language):
        return None
    tokens = _TOKEN.findall(sentence)
    if not tokens:
        return None

    allowed = {name.lower() for name in known}
    allowed |= markers.forms_of_address.get(language) or frozenset()
    allowed |= markers.address_words.get(language) or frozenset()
    allowed |= markers.second_person.get(language) or frozenset()
    frozen = frozenset(allowed)

    address_words = markers.address_words.get(language) or frozenset()

    # A greeting or thanks, then a name, with or without a comma between them.
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in address_words and _candidate(tokens[index + 1], frozen):
            return tokens[index + 1]

    # A sentence that OPENS with a word and a comma. English puts a vocative
    # there ("Jim, the payment plan runs over three years.") and it puts a
    # sentence adverb there just as readily ("Actually, the booking amount is
    # 20 per cent."), so this position needs the stop-list rather than an
    # addressing licence - a leading vocative carries no second-person word of
    # its own, so demanding one would give up the shape entirely.
    openers = markers.sentence_openers.get(language) or frozenset()
    leading = re.match(r"\s*([^\W\d_]+)\s*,", sentence)
    if (
        leading
        and leading.group(1).lower() not in openers
        and _candidate(leading.group(1), frozen)
    ):
        return leading.group(1)

    if not _addresses_someone(tokens, markers, language):
        # Nothing here is speaking to the buyer, so a TRAILING capitalised word
        # is a place or a tower. This is the rule that keeps "It is in Business
        # Bay, Dubai." speakable, and the asymmetry with the leading case is
        # deliberate: English puts places at the end of a sentence and names at
        # the front of one.
        return None

    trailing = re.search(r",\s*([^\W\d_]+)\s*[.!?]*\s*$", sentence)
    if trailing and _candidate(trailing.group(1), frozen):
        return trailing.group(1)
    return None
