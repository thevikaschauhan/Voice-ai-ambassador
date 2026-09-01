"""Latency-masking acknowledgments, loaded from data/prerolls.yaml (docs/04-).

`data/prerolls.yaml` was authored, specified in docs/04-, named in
`interception.py`'s own comments, and read by nothing - the third file in this
repository to be configuration in appearance and a document in fact, after
`disclosures.yaml` and `lexicon.yaml`. The consequence here is smaller than the
disclosure's (a missing filler costs perceived latency, not a legal notice),
but the consequence of having NO loader is not: the Arabic and Hindi lists are
`VERIFY:` and empty, and a `VERIFY:` in a file no loader reads cannot reach the
native-reviewer packet. So the copy nobody may write here was also never being
asked for.

This module is the loader, shaped like its neighbours (`load_fallback_copy`,
`load_disclosures`, `load_lexicon`): one function, an optional path override
for tests, a frozen typed result.

## What it deliberately does not do

It does not choose or play a preroll. Playback is a timing decision in the
voice session - docs/04- plays one only when projected first audio exceeds
~800ms, sparingly, because a filler on every turn reads as a tic - and that
wiring is not this change. What exists after this module is the copy, typed
and enumerable, which is what the packet needs.

## Why an absent language gets no preroll rather than an English one

The disclosure falls back to English on purpose: a notice the buyer may not
read still beats no notice, and the substitution is announced on the event
stream. A preroll is the opposite trade. It exists only to make a slow turn
feel attended to, and an English "one moment while I check that" dropped into
an Arabic call is a seam the buyer hears rather than a seam it hides. So a
language with no authored prerolls simply has none, which is exactly today's
behaviour and costs nothing but the masking.

**Which way this fails: towards no filler.** An empty list, an absent language
and a file that is entirely comments all produce silence-of-filler, never
someone else's language and never a hard failure mid-call. What is refused at
load is the shape a person gets wrong while editing - an unknown language key
(a typo means a list nobody reads), a non-list value, and a non-string item
(YAML reads bare yes/no/on/off as booleans and bare digits as numbers, and
these strings are spoken to a buyer verbatim).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Derived, never hand-copied - the same rule the fallback and disclosure
# loaders follow, and for the same reason: a hand-written language list stops
# demanding copy for a language the day one is added to the Literal.
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)


@dataclass(frozen=True)
class Prerolls:
    """The acknowledgments available per language, absence preserved.

    A tuple rather than a list so a caller cannot append to the loaded copy,
    and so `for_language` can hand the same object to every turn.
    """

    by_language: dict[Language, tuple[str, ...]]

    def languages_covered(self) -> frozenset[Language]:
        """Exactly the languages holding real copy.

        Named as `Lexicon.languages_covered` is, and read the same way: it is
        the honest answer to "which languages can actually mask a slow turn",
        which is a different question from which languages the file mentions.
        """
        return frozenset(
            language for language, lines in self.by_language.items() if lines
        )

    def for_language(self, language: Language) -> tuple[str, ...]:
        return self.by_language.get(language, ())


def load_prerolls(path: Path | None = None) -> Prerolls:
    source = path or _DATA_DIR / "prerolls.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping of language to a list "
            f"of acknowledgments, got {type(document).__name__}."
        )

    unknown = sorted(set(document) - set(_LANGUAGES))
    if unknown:
        raise ValueError(
            f"{source.name}: {', '.join(repr(k) for k in unknown)} is not a "
            f"language this system speaks ({', '.join(_LANGUAGES)}). A "
            "misspelled key is a list of copy nobody will ever play, and "
            "nothing else would notice."
        )

    by_language: dict[Language, tuple[str, ...]] = {}
    for language in _LANGUAGES:
        entries = document.get(language)
        entries = [] if entries is None else entries
        if not isinstance(entries, list):
            raise ValueError(
                f"{source.name}: the prerolls for {language!r} must be a list "
                f"of lines, got {type(entries).__name__}."
            )
        lines: list[str] = []
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError(
                    f"{source.name}: a preroll for {language!r} is empty or "
                    f"not text ({entry!r}). These are spoken to a buyer "
                    "verbatim, so each has to be a non-empty quoted string - "
                    "YAML reads bare yes/no/on/off as booleans and bare digits "
                    "as numbers."
                )
            lines.append(entry.strip())
        by_language[language] = tuple(lines)
    return Prerolls(by_language=by_language)
