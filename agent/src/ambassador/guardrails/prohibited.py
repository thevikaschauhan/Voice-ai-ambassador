"""Prohibited-language validator (docs/03- validator 2).

Patterns live in data/prohibited-patterns.yaml, reviewable by a non-engineer.
Non-English patterns must be WRITTEN by a native speaker, never translated by
the build agent (AGENTS.md).

## What `language` on a pattern means, and the one asymmetric rule

It is PROVENANCE and it is ROUTING, and the routing rule is deliberately
lopsided (issue #14, option 2):

    ENGLISH PATTERNS ALWAYS APPLY, PLUS THE SENTENCE'S OWN LANGUAGE.

`patterns_for()` is that rule and `check_prohibited()` is the only caller.
Read both halves before changing either.

**Why English always applies.** Arabic-English code-switching is the default
Dubai register, not an edge case. A reply in Arabic that slips into English to
promise "guaranteed returns" is caught by the English patterns, and it is the
only kind of ar/hi violation catchable at all today. Filtering purely by the
call's language would silently give that up, which is the mistake this rule
exists to make impossible. A test asserts it in both Arabic and Devanagari
script.

**Why there is any filter at all.** Before this, `language` was loaded and
never read, so the validator read as if it were language-aware and merely
under-populated. It was neither, and the shape of the code made the gap
invisible - that was the finding. The field now decides something, so the
day a native reviewer delivers Arabic patterns they apply to Arabic calls and
not to Hindi ones, without anyone re-deriving the policy under demo pressure.
Today the shipped file is English-only, so the rule is a no-op in effect and
changes no behaviour; that is the point of landing it before the patterns
arrive rather than with them.

**What the filter does not cost.** Patterns are script-specific in practice,
so an Arabic pattern could not match Devanagari text anyway. Where it could -
a future Arabic pattern carrying a Latin fragment, or romanised Hindi - the
filter prevents a false positive on the other language, and a false positive
is a sentence the buyer never hears.

**The failure mode the filter introduces, and its two guards.** A wrong
language code used to be harmless, because everything applied to everything.
Under filtering it silently disables the whole group, which is fail-open on the
compliance validator, so the loader refuses to start on either shape of it:

  An UNKNOWN code (`language: eng`) is rejected outright. It is not English, so
  it never always-applies, and it matches no call language either.

  A VALID WRONG code is the nastier one, because nothing about the group looks
  broken: relabel the English `return_guarantees` group as `ar` and the file
  loads, `ar` calls still catch "risk-free", and English calls stop catching it.
  So the loader also validates the (category, language) MATRIX: no duplicate
  pairs, and exactly one group per supported language for every category that
  appears at all. The relabel then fails twice over - it duplicates the target
  language's slot and empties the source language's - and it fails at start-up
  rather than in the room. This cannot prove the semantic language of arbitrary
  regex text; it catches the ordinary single-field mislabel that the explicit
  empty-slot design makes easy to commit.

## The gap this does not close

`languages_covered()` reports which languages actually have patterns, because
nothing else in the system can tell you. Today that is English alone, so a
violation written wholly in Arabic or Devanagari script matches nothing and
the "no guaranteed returns" claim holds only for English and for English
code-switched into another language. That is a real limit on the product's
central claim, it is disclosed in docs/03- rather than papered over, and it
closes when a native speaker authors patterns - not when anyone here
translates them. `data/prohibited-patterns.yaml` carries the empty `ar` and
`hi` slots, one per category, marked `VERIFY:`, so the reviewer writes into a
structure the loader already validates instead of inventing one.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import yaml

from ..schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"

# The languages the product offers, from the one place they are declared. A
# pattern authored for anything else is a typo, and since filtering began a
# typo silently disables the group instead of being harmless.
_LANGUAGES: frozenset[str] = frozenset(get_args(Language))

# English is the always-applies language: see the module docstring. Named
# rather than inlined so the asymmetry is greppable.
_ALWAYS = "en"


@dataclass(frozen=True)
class ProhibitedPattern:
    category: str
    # Provenance AND routing, under one asymmetric rule: English always
    # applies, plus the sentence's own language. Read the module docstring
    # before changing how this is used - the asymmetry is the whole design.
    language: str
    regex: re.Pattern


def _validate_matrix(source_name: str, groups: list[dict]) -> None:
    """Every category that appears must declare exactly one slot per language.

    The guard for a mislabelled `language`, which routing turned from harmless
    into a silent switch-off - see the module docstring. Runs after per-group
    validation so a malformed group still gets its own specific message, and
    before anything is returned so a bad matrix cannot reach a call.
    """
    slots: dict[tuple[str, str], int] = {}
    for index, group in enumerate(groups):
        key = (group["category"], group["language"])
        if key in slots:
            raise ValueError(
                f"{source_name}: groups {slots[key]} and {index} both declare "
                f"({key[0]!r}, {key[1]!r}). Exactly one slot per language per "
                "category, because a duplicate is what a mislabelled "
                "'language' looks like from here: the group that was moved IN "
                "duplicates this one, and the language it moved OUT of "
                "silently stops being checked."
            )
        slots[key] = index

    for category in sorted({name for name, _ in slots}):
        missing = sorted(
            language for language in _LANGUAGES if (category, language) not in slots
        )
        if missing:
            raise ValueError(
                f"{source_name}: category {category!r} has no slot for "
                f"{'/'.join(missing)}. Every category declares one group per "
                f"language ({'/'.join(sorted(_LANGUAGES))}), empty ones as "
                "'patterns: []'. Two reasons: a category present for one "
                "language and absent for another is how a mislabelled "
                "'language' hides, and the empty slots are where a native "
                "reviewer writes - a category with no slot is a category "
                "nobody will be asked about."
            )


def load_patterns(path: Path | None = None) -> list[ProhibitedPattern]:
    """Compile the file, or say what is wrong with it in one sentence.

    Every failure here already landed at start-up rather than mid-call, so
    none of this is a correctness fix. It is that the next person to edit this
    file is an engineer transcribing a native reviewer's patterns, and
    `KeyError: 'language'` with no filename and no line is a poor thing to hand
    them. The sibling loaders in `data/` all report their own failures; this
    one did not.
    """
    source = path or _DATA_DIR / "prohibited-patterns.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    groups = [] if raw is None else raw
    if not isinstance(groups, list):
        raise ValueError(
            f"{source.name}: the file must be a list of pattern groups, got "
            f"{type(groups).__name__}."
        )

    compiled: list[ProhibitedPattern] = []
    validated: list[dict] = []
    for index, group in enumerate(groups):
        where = f"{source.name}: group {index}"
        if not isinstance(group, dict):
            raise ValueError(f"{where} is a {type(group).__name__}, not a mapping.")
        for field in ("category", "language"):
            if not group.get(field):
                raise ValueError(
                    f"{where} ({group.get('category', 'unnamed')!r}) has no "
                    f"{field!r}. 'language' decides when the group applies - "
                    "English always, plus the sentence's own language - so it "
                    "is required and must be one of "
                    f"{'/'.join(sorted(_LANGUAGES))}."
                )
        if group["language"] not in _LANGUAGES:
            raise ValueError(
                f"{where} ({group['category']!r}) has language "
                f"{group['language']!r}, which is not one of "
                f"{'/'.join(sorted(_LANGUAGES))}. Since patterns are routed by "
                "language a code the system does not offer disables the whole "
                "group silently: it is not English, so it never "
                "always-applies, and it matches no call either. A typo here "
                "used to be harmless and now switches off a compliance check."
            )
        # An ABSENT 'patterns' key is an omission; an explicitly empty list is a
        # declaration. `ar` and `hi` ship as empty lists on purpose, so a native
        # reviewer writes into a slot the loader already validates rather than
        # inventing the structure, and so the gap is data rather than a comment.
        if "patterns" not in group or group["patterns"] is None:
            raise ValueError(
                f"{where} ({group['category']!r}) has no 'patterns'. Write "
                "'patterns: []' if the slot is deliberately empty pending "
                "native review; leaving the key out reads as an accident."
            )
        patterns = group["patterns"]
        if not isinstance(patterns, list):
            raise ValueError(
                f"{where}: 'patterns' must be a list, got {type(patterns).__name__}."
            )
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise ValueError(
                    f"{where}: every pattern must be a quoted regular "
                    f"expression, got {type(pattern).__name__}."
                )
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"{where}: {pattern!r} is not a valid regular expression "
                    f"({exc}). Remember YAML needs the backslashes doubled."
                ) from exc
            compiled.append(
                ProhibitedPattern(
                    category=group["category"],
                    language=group["language"],
                    regex=regex,
                )
            )
        validated.append(group)
    _validate_matrix(source.name, validated)
    return compiled


def languages_covered(patterns: list[ProhibitedPattern]) -> frozenset[str]:
    """The languages someone competent has actually WRITTEN patterns for.

    Authorship, not protection: English patterns apply in every language, so a
    language missing from this set is still covered against code-switched
    English. What it is not covered against is a violation written wholly in
    its own script, and that is exactly what this set exists to surface.

    A declared-but-empty slot (`patterns: []`) contributes nothing here, which
    is deliberate: declaring `ar` in the file must not read as covering it.

    Emitted at session start so the demo record states the true coverage. The
    alternative is a system that looks equally protected in every language it
    offers, which is the impression the unused `language` field gave.
    """
    return frozenset(p.language for p in patterns)


def patterns_for(
    patterns: list[ProhibitedPattern], language: str
) -> list[ProhibitedPattern]:
    """The patterns that apply to a sentence in `language`.

    English always, plus that language's own. The asymmetry is load-bearing -
    see the module docstring - and it is a separate named function so a caller
    cannot half-implement it.
    """
    return [p for p in patterns if p.language in (_ALWAYS, language)]


def check_prohibited(
    text: str, patterns: list[ProhibitedPattern], language: str
) -> list[str]:
    """Return 'category: matched text' for each hit. Empty list = pass.

    `language` is required, with no default. A default would make the routing
    rule skippable by omission on the compliance validator, and the whole
    finding behind this code was a language field that looked consulted and
    was not.
    """
    hits: list[str] = []
    for p in patterns_for(patterns, language):
        m = p.regex.search(text)
        if m:
            hits.append(f"{p.category}: {m.group(0)!r}")
    return hits
