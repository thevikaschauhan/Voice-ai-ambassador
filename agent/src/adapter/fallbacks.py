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
from typing import Any, Final

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

_LANGUAGES: Final[tuple[Language, ...]] = ("en", "ar", "hi")


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
    entries = (raw or {}).get(kind) or {}
    copy: dict[Language, str] = {}
    for language in _LANGUAGES:
        text = str(entries.get(language) or "").strip()
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
