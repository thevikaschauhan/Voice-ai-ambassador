"""The ambassador's given name, per language, from data/ambassadors.yaml.

The client named the English ambassador Jane. The name reaches a buyer through
two paths - the opening disclosure speaks it, and the system prompt tells the
model what it is called - and it reaches the web surface as the orb label, so
it is one file read by both services rather than a constant in either.

## Why a name is not language copy

AGENTS.md forbids authoring copy in a language nobody here speaks. A given
name is not that: it is product identity, chosen by the client, and it is the
same word whoever is listening. What IS a native-reviewer question is how that
word should be written in Arabic or Devanagari and how it should be said
aloud, so the file carries `VERIFY:` markers for those and the reviewer packet
asks the question. Transliterating it here would be exactly the thing the rule
exists to prevent, dressed as a small convenience.

## Why an empty name is not an error

`disclosures.py` treats an empty entry as a language that may not open a call,
because a call with no disclosure is a call that may not happen. This loader
deliberately does NOT copy that rule. An unnamed ambassador is a working
ambassador: the disclosure falls back to the sentence that shipped before
names existed, the prompt says nothing about a name, and the language behaves
exactly as it did. So absence is a state with a defined behaviour, not a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from .schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Derived from the Literal rather than hand-written, the same rule every other
# loader here follows: a fourth language must not silently stop being asked
# for a name.
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)


@dataclass(frozen=True)
class Ambassadors:
    """Given names per language, with absence preserved rather than filled in.

    Absence is not filled from English on purpose. A buyer on a Hindi call
    hearing an English disclosure is a documented degradation with its own
    event field; a buyer on a Hindi call hearing an English NAME inside Hindi
    copy would be a silent one nobody decided on.
    """

    names: dict[Language, str]

    @property
    def named(self) -> frozenset[Language]:
        return frozenset(language for language, name in self.names.items() if name)

    def name_for(self, language: Language) -> str:
        """The name to use, or the empty string when the ambassador is unnamed.

        The empty string is the caller's signal to use the unnamed wording. It
        is returned rather than None because every caller substitutes it into
        text, and `None` would reach a template as the word "None" the first
        time somebody forgot to check.
        """
        return self.names.get(language, "")


def load_ambassadors(path: Path | None = None) -> Ambassadors:
    """Parse the file, or fail in front of whoever edited it."""
    source = path or _DATA_DIR / "ambassadors.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping of language to "
            f"ambassador name, got {type(document).__name__}."
        )

    names: dict[Language, str] = {}
    for language in _LANGUAGES:
        value = document.get(language)
        if value is None:
            value = ""
        elif not isinstance(value, str):
            # The YAML trap this repository has hit twice: bare `no` and `on`
            # load as booleans and bare digits as numbers. This value is spoken
            # to a buyer and put in front of a model, so it is refused rather
            # than coerced - `str(False)` would introduce an ambassador called
            # "False" and the file would look fine.
            raise ValueError(
                f"{source.name}: the name for {language!r} is a "
                f"{type(value).__name__}, not text. It is spoken to a buyer, "
                "so it has to be a quoted string."
            )
        names[language] = value.strip()

    return Ambassadors(names=names)
