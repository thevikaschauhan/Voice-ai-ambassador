"""Pronunciation respelling applied to the text handed to TTS (docs/04-).

`data/lexicon.yaml` was authored, documented and read by nothing. Every
`respell` value existed and none reached the synthesiser, so "Binghatti" was
spoken from its literal spelling - and #4's "verified by ear in every shipped
voice" could not be carried out, because the thing under test was not in the
path.

## Where this runs, and why there

In `tts_node`, after guardrails and verbalisation. Respelling destroys the
word, the same way verbalisation destroys digits, so it has to come last: the
transcript, the audit and the ambassador view keep the real words, and only
the synthesiser sees "bin-GAH-tee". Nothing downstream of TTS reads this text.

## Why per language

A respelling is instructions to a voice in that voice's own orthography.
Handing "bin-GAH-tee" to an Arabic voice does not fix a mispronunciation, it
picks a different one, quite possibly a spelled-out one. So a term is respelled
only in a language somebody competent in that language has written an entry
for, and passed through untouched otherwise. Untouched is exactly today's
behaviour, so a language with no entries is no worse off than before this
module existed.

## The chunk boundary

Text arrives as a stream. Today's chunks are whole sentences - `guarded_stream`
validates and yields per sentence - so a term cannot straddle two, and applying
per chunk would be correct. It would also be silently wrong the day anything
upstream re-chunks more finely, and the failure mode is the client's own name
mispronounced with nothing in any log to show why. So this buffers to sentence
boundaries: a term never spans one, and because the first chunk already ends at
a boundary, nothing is held back and no latency is added on the path that
matters. TTS first audio is one of the two largest remaining budget
components (docs/04-) and this must not touch it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from ambassador.schemas import Language

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# The same boundary `interception.split_sentences` uses, including the Arabic
# question mark and the Devanagari danda. Duplicated deliberately rather than
# imported: that module is the guardrail path and this one is the synthesis
# path, and coupling them would mean a change made for one silently retimed the
# other.
_BOUNDARY: Final = re.compile(r"(?<=[.!?؟।])\s+")


@dataclass(frozen=True)
class Lexicon:
    """Compiled respellings, per language, longest term first.

    Longest first so "Jumeirah Village Circle" is replaced as a unit rather
    than having a bare "Jumeirah" rewritten inside it.
    """

    by_language: dict[Language, tuple[tuple[re.Pattern[str], str], ...]]

    def languages_covered(self) -> frozenset[Language]:
        return frozenset(
            language for language, entries in self.by_language.items() if entries
        )

    def apply(self, text: str, language: Language) -> str:
        for pattern, respelling in self.by_language.get(language, ()):
            # A function, not the string: `re.sub` reads a replacement STRING
            # as a template, so a backslash in a respelling is an escape and
            # `\1` is a group reference. Both raise `re.PatternError`, and this
            # runs inside `tts_node`, so the raise would surface as a turn with
            # no audio - the one outcome AGENTS.md rules out absolutely. The
            # values likeliest to contain a backslash are the native-authored
            # ones this file exists to receive.
            text = pattern.sub(lambda _match, r=respelling: r, text)
        return text


def _compile_term(term: str) -> re.Pattern[str]:
    """A term, tolerant of the spacing a model actually writes.

    `Jacob & Co` has to match `Jacob&Co`, which is how the docs write it and
    how a model will too, so runs of whitespace in the term become optional
    whitespace in the pattern. The lookarounds are `\\w` rather than `\\b`
    because `\\b` is meaningless next to `&`.
    """
    body = r"\s*".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def load_lexicon(path: Path | None = None) -> Lexicon:
    source = path or _DATA_DIR / "lexicon.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    entries = [] if raw is None else raw
    if not isinstance(entries, list):
        raise ValueError(
            f"{source.name}: the file must be a list of terms, got "
            f"{type(entries).__name__}."
        )

    collected: dict[str, list[tuple[str, str]]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("term"):
            raise ValueError(f"{source.name}: every entry needs a 'term'.")
        term = str(entry["term"])
        respell = entry.get("respell") or {}
        if not isinstance(respell, dict):
            raise ValueError(
                f"{source.name}: 'respell' for {term!r} must map a language code "
                "to a respelling. A single string used to mean English, and "
                "applying an English respelling to an Arabic voice is a defect, "
                "not a default."
            )
        for language, respelling in respell.items():
            if not isinstance(respelling, str) or not respelling.strip():
                raise ValueError(
                    f"{source.name}: the {language!r} respelling for {term!r} is "
                    "empty or not text. It is substituted into speech verbatim."
                )
            collected.setdefault(language, []).append((term, respelling.strip()))

    by_language: dict[Language, tuple[tuple[re.Pattern[str], str], ...]] = {}
    for language, pairs in collected.items():
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        by_language[language] = tuple(  # type: ignore[index]
            (_compile_term(term), respelling) for term, respelling in pairs
        )
    return Lexicon(by_language=by_language)


def respell_stream(
    source: AsyncIterable[str], lexicon: Lexicon, language: Language
) -> AsyncIterable[str]:
    """Respell a stream of text without letting a term split across chunks.

    Returns the source object itself when the language has no entries, so a
    language nobody has authored pays nothing - not even the buffering.
    """
    if not lexicon.by_language.get(language):
        return source
    return _buffered(source, lexicon, language)


async def _buffered(
    source: AsyncIterable[str], lexicon: Lexicon, language: Language
) -> AsyncIterator[str]:
    """Emit everything up to and including the last sentence boundary seen.

    Cutting at the boundary rather than splitting on it keeps the text exactly
    as it arrived, whitespace included. Splitting consumes the separator, and
    a synthesiser handed "Handover is Q4 2026." immediately followed by "The
    price is..." with the space eaten reads them as one run.
    """
    carry = ""
    async for chunk in source:
        carry += chunk
        boundaries = list(_BOUNDARY.finditer(carry))
        if boundaries:
            cut = boundaries[-1].end()
            yield lexicon.apply(carry[:cut], language)
            carry = carry[cut:]
    if carry:
        yield lexicon.apply(carry, language)
