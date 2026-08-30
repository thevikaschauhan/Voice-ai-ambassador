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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Disclosures:
    """Disclosure copy per language, with absence preserved rather than filled.

    `certified` is exactly the set of languages holding real copy. It is the
    readiness signal the session gate reads, so it must not be inferred from
    anything softer than the presence of the text itself.
    """

    copy: dict[Language, str]

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
    for language in _LANGUAGES:
        value = document.get(language)
        if value is None:
            value = ""
        elif not isinstance(value, str):
            # Same YAML trap the fallback loader documents: bare yes/no/on/off
            # parse as booleans and bare digits as numbers, and this string is
            # spoken to a buyer verbatim.
            raise ValueError(
                f"{source.name}: the disclosure for {language!r} is a "
                f"{type(value).__name__}, not text. This is spoken to a buyer "
                "verbatim, so it has to be a quoted string."
            )
        copy[language] = value.strip()

    if not copy[_SELF_CERTIFIED]:
        raise ValueError(
            f"{source.name}: no disclosure for {_SELF_CERTIFIED!r}. Every call "
            "opens with a disclosure, and this is the copy an uncertified "
            "language falls back to, so its absence leaves nothing to say."
        )
    return Disclosures(copy=copy)


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
) -> tuple[str, Language]:
    """The disclosure to speak, or a refusal to open the call at all.

    Returns the copy and the language it is genuinely in. When those disagree,
    the caller is expected to say so on the event stream: a buyer hearing an
    English disclosure on an Arabic call is a documented, deliberate
    degradation, and the record has to show it was that rather than a
    certified Arabic opening.
    """
    if disclosures.is_certified(language):
        return disclosures.copy[language], language

    if not allow_uncertified:
        raise UncertifiedLanguageError(
            f"no native-authored disclosure for {language!r}, so a call cannot "
            f"open in it. Author data/disclosures.yaml for {language!r} with a "
            "native speaker, or set ALLOW_UNCERTIFIED_LANGUAGE=true to demo "
            f"the degraded path, which opens in {_SELF_CERTIFIED!r} instead."
        )
    return disclosures.for_language(language)
