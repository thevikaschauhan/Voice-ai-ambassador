"""When the buyer is ending the call, and what the agent says before it does.

Deterministic and in the core, not a model tool, because the two failure
directions are not symmetric. A missed goodbye leaves the call behaving exactly
as it does today - the buyer closes the tab, as they always have. A FALSE
goodbye hangs up on a live buyer mid-conversation. Asking a model to make a
call that consequential also makes it depend on the LLM being reachable, which
the first hosted run showed is not a given (three upstream 429s in eleven
turns).

So the match is conservative in a specific way: a farewell has to be what the
utterance IS, rather than a word that appears inside it. At least one closing
phrase must match, and every remaining token must be a courtesy. That is the
rule that keeps "before we say goodbye, what about the payment plan" a question
about the payment plan, and it is the same shape as
`recognition.is_failed_recognition`, which asks whether every token is noise.

A courtesy never fires on its own. "Thank you" in the middle of a call is not
an ending, and treating it as one would be the expensive mistake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .figures import normalise_digits

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Any letter or digit in any script, so the token split works in Arabic and
# Devanagari without an authored list. Same pair as `recognition.py`.
_CONTENT = re.compile(r"[^\W_]", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Farewells:
    """The closing tables and authored copy, per language."""

    phrases: dict[str, tuple[tuple[str, ...], ...]]
    courtesies: dict[str, frozenset[str]]
    speech: dict[str, str]

    def detects(self, language: str) -> bool:
        """Whether a closing line can be recognised in this language at all.

        False is a supported state, not a broken one: with no authored phrases
        the call ends the way it did before this existed. A guessed phrase list
        would be the dangerous option.
        """
        return bool(self.phrases.get(language))

    def farewell_speech(self, language: str) -> str:
        """The authored farewell, falling back to English.

        English rather than silence, for the reason `fallbacks.yaml` gives: a
        turn must never end in silence. A call in a language with no authored
        disclosure already runs in English today, so this matches the rest of
        that call rather than introducing a language switch at the end of it.
        """
        return self.speech.get(language) or self.speech["en"]


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(normalise_digits(text).lower())


def load_farewells(path: Path | None = None) -> Farewells:
    source = path or _DATA_DIR / "farewells.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping, got {type(raw).__name__}."
        )

    def _strings(section: str, language: str, values: Any) -> list[str]:
        out: list[str] = []
        for value in values or []:
            if not isinstance(value, str):
                # The YAML 1.1 boolean trap: bare no/yes/on/off load as
                # booleans, and a coerced boolean is a word that can never
                # match. Loud here beats a dead entry nobody notices.
                raise ValueError(
                    f"{source.name}: {section}.{language} contains {value!r}, "
                    "not text. Quote the entry in the data file."
                )
            out.append(value)
        return out

    phrases: dict[str, tuple[tuple[str, ...], ...]] = {}
    for language, values in (raw.get("phrases") or {}).items():
        runs = [
            tuple(_tokens(entry)) for entry in _strings("phrases", language, values)
        ]
        # A phrase that tokenises to nothing could never match and would sit in
        # the table looking like coverage.
        phrases[language] = tuple(run for run in runs if run)

    courtesies: dict[str, frozenset[str]] = {}
    for language, values in (raw.get("courtesies") or {}).items():
        words: set[str] = set()
        for entry in _strings("courtesies", language, values):
            words.update(_tokens(entry))
        courtesies[language] = frozenset(words)

    speech: dict[str, str] = {}
    for language, value in (raw.get("speech") or {}).items():
        if not isinstance(value, str) or not value.strip():
            # Refused at load rather than discovered when a call tries to end.
            # This is spoken product copy and a missing one is silence.
            raise ValueError(
                f"{source.name}: speech.{language} is empty. Every language "
                "needs a farewell, or a call in it ends in silence."
            )
        speech[language] = " ".join(value.split())
    if "en" not in speech:
        raise ValueError(f"{source.name}: speech.en is required as the fallback.")

    return Farewells(phrases=phrases, courtesies=courtesies, speech=speech)


def _match_at(tokens: list[str], index: int, run: tuple[str, ...]) -> bool:
    return tuple(tokens[index : index + len(run)]) == run


def is_farewell(utterance: str, farewells: Farewells, language: str) -> bool:
    """Is this utterance the buyer closing the conversation?

    Longest phrase first, so "that is all" is consumed whole rather than
    leaving "is all" to be judged as leftovers.
    """
    text = normalise_digits(utterance)
    if not _CONTENT.search(text):
        return False
    runs = farewells.phrases.get(language) or ()
    if not runs:
        return False
    tokens = _tokens(text)
    ordered = sorted(runs, key=len, reverse=True)
    matched = False
    index = 0
    leftovers: list[str] = []
    while index < len(tokens):
        for run in ordered:
            if _match_at(tokens, index, run):
                matched = True
                index += len(run)
                break
        else:
            leftovers.append(tokens[index])
            index += 1
    if not matched:
        return False
    courtesies = farewells.courtesies.get(language) or frozenset()
    return all(token in courtesies for token in leftovers)
