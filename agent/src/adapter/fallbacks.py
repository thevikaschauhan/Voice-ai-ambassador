"""Composed failure speech, loaded from data/fallbacks.yaml.

The bridge and the fallback are spoken product copy - a buyer hears them - so
they live in `data/` beside the disclosure and the prerolls, where a
non-engineer can read and a native speaker can author them. This module is the
loader, shaped like the core's own (`guardrails.prohibited.load_patterns`,
`verbalise.load_spoken_forms`): one function, an optional path override for
tests, and a frozen typed result.

It is adapter code rather than core code because the copy is a property of the
interception hook's recovery policy (docs/01-), not of the grounding rules the
core owns. It reads a file, which the core may not do.

The one thing this loader does beyond parsing is refuse an empty string. Every
other path in the system can degrade to digits or to English; this one cannot
degrade to anything, because it is what speaks when everything else has
failed, and AGENTS.md is absolute that a turn never ends in silence. A missing
translation must fail at load, in front of whoever edited the file, rather
than raise a KeyError mid-call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, get_args

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Derived, never hand-copied. A hand-written copy of the language set drifts
# silently the day a language is added to the Literal: this loader would stop
# demanding copy for it, the data file would pass, and the KeyError would land
# mid-call on the one path that exists so a turn never ends in silence.
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)


@dataclass(frozen=True)
class FallbackCopy:
    """The two recoveries, each complete across every language.

    They are separate fields rather than one table because they are separate
    claims (docs/01-): a bridge means the buyer heard a seam, a fallback means
    the composed copy WAS the reply.
    """

    bridge: dict[Language, str]
    fallback: dict[Language, str]


def _block(raw: Any, kind: str, source: Path) -> dict[Language, str]:
    """One block of the file, complete and text-only, or a curated ValueError.

    Three separate failures, all of which used to reach a caller as something
    worse than a message. A document or a block of the wrong shape raised a raw
    AttributeError from inside the loader; a non-string scalar was coerced with
    `str()` and shipped to TTS as speech ("True", "985000"); and a falsy scalar
    was coerced to "" and then misreported as missing copy. YAML makes all three
    easy to write by accident: bare `yes`/`no`/`on`/`off` parse as booleans and
    bare digits as numbers, so `en: no` is a boolean, not the word.
    """
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping of block name to "
            f"per-language copy, got {type(document).__name__}."
        )
    entries = document.get(kind)
    entries = {} if entries is None else entries
    if not isinstance(entries, dict):
        raise ValueError(
            f"{source.name}: '{kind}' must be a mapping of language to copy, "
            f"got {type(entries).__name__}."
        )
    copy: dict[Language, str] = {}
    for language in _LANGUAGES:
        value = entries.get(language)
        if value is None:
            value = ""
        elif not isinstance(value, str):
            raise ValueError(
                f"{source.name}: '{kind}' copy for {language!r} is a "
                f"{type(value).__name__}, not text. This value is spoken to a "
                "buyer verbatim, so it has to be a quoted string - YAML reads "
                "bare yes/no/on/off as booleans and bare digits as numbers."
            )
        text = value.strip()
        if not text:
            raise ValueError(
                f"{source.name}: '{kind}' has no copy for {language!r}. This is "
                "the speech that plays when the model fails, so an empty string "
                "is a silent turn, which AGENTS.md does not permit."
            )
        copy[language] = text
    return copy


def load_fallback_copy(path: Path | None = None) -> FallbackCopy:
    source = path or _DATA_DIR / "fallbacks.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return FallbackCopy(
        bridge=_block(raw, "bridge", source),
        fallback=_block(raw, "fallback", source),
    )
