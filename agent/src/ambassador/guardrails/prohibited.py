"""Prohibited-language validator (docs/03- validator 2).

Patterns live in data/prohibited-patterns.yaml, reviewable by a non-engineer.
Non-English patterns must be WRITTEN by a native speaker, never translated by
the build agent (AGENTS.md).

## What `language` on a pattern means, and what it does not

It is PROVENANCE: the language whose speech this pattern was authored to
catch, and therefore the competence its author needed. It is not routing.
Every pattern is matched against every sentence regardless of the sentence's
language, deliberately, and the field was previously loaded and never read at
all - which read as if the validator were language-aware and merely
under-populated. It was neither.

Applying everything is the safe direction, for the same reason figure
extraction errs toward extracting more: an over-matched pattern blocks a
sentence, an under-matched one speaks an unverified one. Two concrete
consequences:

  Code-switching is covered. Arabic-English is the default Dubai register,
  not an edge case, and a reply in Arabic that slips into English to say
  "guaranteed returns" is caught by the English patterns. Routing by the
  sentence's language would silently give that up.

  Cross-language false positives are close to impossible in practice. The
  scripts differ, and where they do not (romanised Hindi) the vocabulary
  does. A blocked sentence is a recoverable outcome anyway; a spoken
  guarantee is not.

## The gap this does not close

`languages_covered()` reports which languages actually have patterns, because
nothing else in the system can tell you. Today that is English alone, so a
violation written in Arabic or Devanagari script matches nothing and the "no
guaranteed returns" claim holds only for English and for English
code-switched into another language. That is a real limit on the product's
central claim, it is disclosed rather than papered over, and it closes when a
native speaker authors patterns - not when anyone here translates them.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"


@dataclass(frozen=True)
class ProhibitedPattern:
    category: str
    # Provenance, not routing. See the module docstring before using this to
    # filter anything.
    language: str
    regex: re.Pattern


def load_patterns(path: Path | None = None) -> list[ProhibitedPattern]:
    raw = yaml.safe_load(
        (path or _DATA_DIR / "prohibited-patterns.yaml").read_text(encoding="utf-8")
    )
    compiled: list[ProhibitedPattern] = []
    for group in raw:
        for pattern in group["patterns"]:
            compiled.append(
                ProhibitedPattern(
                    category=group["category"],
                    language=group["language"],
                    regex=re.compile(pattern, re.IGNORECASE),
                )
            )
    return compiled


def languages_covered(patterns: list[ProhibitedPattern]) -> frozenset[str]:
    """The languages someone competent has actually written patterns for.

    Emitted at session start so the demo record states the true coverage. The
    alternative is a system that looks equally protected in every language it
    offers, which is the impression the unused `language` field gave.
    """
    return frozenset(p.language for p in patterns)


def check_prohibited(text: str, patterns: list[ProhibitedPattern]) -> list[str]:
    """Return 'category: matched text' for each hit. Empty list = pass.

    Every pattern is tried against every sentence whatever language it is in.
    That is the point, not an oversight - see the module docstring.
    """
    hits: list[str] = []
    for p in patterns:
        m = p.regex.search(text)
        if m:
            hits.append(f"{p.category}: {m.group(0)!r}")
    return hits
