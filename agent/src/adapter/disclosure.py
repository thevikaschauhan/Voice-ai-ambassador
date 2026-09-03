"""The call-opening AI disclosure, loaded from data/disclosures.yaml.

This module exists because the disclosure was specified, written, and never
spoken. `data/disclosures.yaml` had no loader anywhere in the codebase, no
call site said it, and `prompts.py` tells the model "The call opening and AI
disclosure are handled by the system, not by you" - so the model was
explicitly instructed not to do it and nothing else did. The net effect was a
voice agent that never disclosed it was one, in any language, English
included.

Fixed copy, never model-generated (ADR-013): a disclosure the model composes
can vary, and one that varies is not a disclosure. It is also the one piece of
speech that ignores barge-in, so it always completes (docs/04-).

The copy says "transcribed", not "recorded", and that wording is load-bearing:
the POC stores no raw audio, and a notice must match what is actually
retained.

## Why an absent disclosure blocks a language

A language with no disclosure is a language this system may not open a call
in. That is not a style rule - it is the disclosure. So the presence of
native-authored disclosure copy doubles as the readiness signal for a
language, which is why this loader reports absence as a distinct, typed state
rather than papering over it with the English string. Today `ar` and `hi` are
both empty and carry `VERIFY:` markers, because nobody on the build team may
author or certify copy in a language they do not speak (AGENTS.md).

`ALLOW_UNCERTIFIED_LANGUAGE` exists for the demo of graceful degradation that
docs/04- calls for - shipping Arabic and showing it degrade reads better in a
Dubai boardroom than quietly avoiding Arabic. It never results in silence: an
uncertified language falls back to the English disclosure, which is a
disclosure the buyer may not read rather than no disclosure at all, and the
substitution is announced on the event stream so it can never be mistaken for
the real thing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Derived, never hand-copied - the same rule the fallback loader follows, and
# for the same reason: a hand-written language list stops demanding copy for a
# language the day one is added to the Literal.
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)

# The language this build team self-certifies. AGENTS.md is absolute that no
# other language may be authored or signed off here.
_SELF_CERTIFIED: Final[Language] = "en"


# The one substitution the copy may carry. Named rather than inlined so a
# search for it finds the data file, the loader and the test together.
NAME_PLACEHOLDER: Final = "{name}"


@dataclass(frozen=True)
class Disclosures:
    """Disclosure copy per language, with absence preserved rather than filled.

    `copy` is the sentence spoken when the ambassador is unnamed, and it is
    what `certified` reads: a language is openable when it has that sentence,
    which is exactly the rule that applied before names existed. `named_copy`
    is the optional template carrying `{name}` and is used only when there is a
    name to put in it.

    Two fields rather than one rendered string because the choice depends on
    `data/ambassadors.yaml`, which this loader deliberately does not read - the
    disclosure knows what it can say, the caller knows who is saying it.
    """

    copy: dict[Language, str]
    named_copy: dict[Language, str] = field(default_factory=dict)

    def speak(self, language: Language, name: str) -> str:
        """The disclosure for this language, with the ambassador's name in it.

        Falls back to the unnamed sentence whenever there is nothing to
        substitute or nowhere to substitute it, so no combination of a missing
        name and a missing template can produce a sentence with a hole in it.
        """
        template = self.named_copy.get(language, "")
        if not name or not template:
            return self.copy.get(language, "")
        return template.replace(NAME_PLACEHOLDER, name)

    @property
    def certified(self) -> frozenset[Language]:
        return frozenset(language for language, text in self.copy.items() if text)

    def is_certified(self, language: Language) -> bool:
        return bool(self.copy.get(language))

    def for_language(self, language: Language) -> tuple[str, Language]:
        """The copy to speak, and the language it is actually in.

        The two differ when an uncertified language falls back to English, and
        the caller needs both: one to synthesise, one to report honestly.
        """
        if self.is_certified(language):
            return self.copy[language], language
        return self.copy[_SELF_CERTIFIED], _SELF_CERTIFIED


def _text(source: str, language: Language, value: Any, key: str | None) -> str:
    """One scalar of disclosure copy, or a refusal.

    Same YAML trap the fallback loader documents: bare yes/no/on/off parse as
    booleans and bare digits as numbers, and this string is spoken to a buyer
    verbatim. Shared by both shapes so the named template cannot quietly skip
    the check the plain string gets.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        where = f"{language!r}" if key is None else f"{key!r} for {language!r}"
        raise ValueError(
            f"{source}: the disclosure {where} is a {type(value).__name__}, "
            "not text. This is spoken to a buyer verbatim, so it has to be a "
            "quoted string."
        )
    return value.strip()


def load_disclosures(path: Path | None = None) -> Disclosures:
    """Parse the file, or fail in front of whoever edited it.

    The self-certified language is the one hard requirement: without it there
    is no copy to fall back to, and the gate below would have nothing to offer
    an uncertified language but silence.
    """
    source = path or _DATA_DIR / "disclosures.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping of language to "
            f"disclosure copy, got {type(document).__name__}."
        )

    copy: dict[Language, str] = {}
    named_copy: dict[Language, str] = {}
    for language in _LANGUAGES:
        value = document.get(language)
        if isinstance(value, dict):
            # The named shape. `unnamed` is the disclosure and is what makes the
            # language openable; `named` is the optional template. Read in that
            # order so a mapping that forgot `unnamed` fails here rather than at
            # the readiness gate, where the message would blame the wrong thing.
            unnamed = _text(source.name, language, value.get("unnamed"), "unnamed")
            if not unnamed:
                raise ValueError(
                    f"{source.name}: {language!r} has no 'unnamed' disclosure. "
                    "That sentence is the one that makes the language openable "
                    "and the one spoken if the ambassador's name is ever "
                    "cleared, so a named template cannot stand alone."
                )
            template = _text(source.name, language, value.get("named"), "named")
            if template and NAME_PLACEHOLDER not in template:
                raise ValueError(
                    f"{source.name}: the 'named' disclosure for {language!r} "
                    f"contains no {NAME_PLACEHOLDER}, so naming the ambassador "
                    "would change nothing about what the buyer hears. Either "
                    "put the placeholder in it or delete the key."
                )
            copy[language] = unnamed
            if template:
                named_copy[language] = template
        else:
            copy[language] = _text(source.name, language, value, None)

    if not copy[_SELF_CERTIFIED]:
        raise ValueError(
            f"{source.name}: no disclosure for {_SELF_CERTIFIED!r}. Every call "
            "opens with a disclosure, and this is the copy an uncertified "
            "language falls back to, so its absence leaves nothing to say."
        )
    return Disclosures(copy=copy, named_copy=named_copy)


class UncertifiedLanguageError(RuntimeError):
    """Raised at start-up when a language has no native-authored disclosure.

    At start-up on purpose. The alternative is discovering mid-call that the
    agent has nothing to disclose, and by then it is already talking to a
    buyer.
    """


def resolve_opening(
    disclosures: Disclosures,
    language: Language,
    *,
    allow_uncertified: bool,
    names: Mapping[Language, str] | None = None,
) -> tuple[str, Language]:
    """The disclosure to speak, or a refusal to open the call at all.

    Returns the copy and the language it is genuinely in. When those disagree,
    the caller is expected to say so on the event stream: a buyer hearing an
    English disclosure on an Arabic call is a documented, deliberate
    degradation, and the record has to show it was that rather than a
    certified Arabic opening.

    `names` is the ambassador name per language, and the lookup is keyed on the
    language ACTUALLY SPOKEN rather than the one requested. That matters in the
    degraded case: an Arabic call that falls back to the English sentence must
    use the English name, because the name is being read inside English copy.
    Omitted, the ambassador is unnamed and the sentence is the one that shipped
    before names existed - which is what every existing caller and test gets.
    """
    lookup = dict(names or {})
    if disclosures.is_certified(language):
        return disclosures.speak(language, lookup.get(language, "")), language

    if not allow_uncertified:
        raise UncertifiedLanguageError(
            f"no native-authored disclosure for {language!r}, so a call cannot "
            f"open in it. Author data/disclosures.yaml for {language!r} with a "
            "native speaker, or set ALLOW_UNCERTIFIED_LANGUAGE=true to demo "
            f"the degraded path, which opens in {_SELF_CERTIFIED!r} instead."
        )
    _, spoken_language = disclosures.for_language(language)
    # Re-rendered rather than reusing the copy `for_language` returned: that one
    # is the unnamed sentence, and the name belongs to the language actually
    # being spoken, not the one that was asked for.
    speech = disclosures.speak(spoken_language, lookup.get(spoken_language, ""))
    return speech, spoken_language
