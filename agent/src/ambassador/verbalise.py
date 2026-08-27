"""Closed-set verbalisation (ADR-009).

Only figures in the allowed set can reach this module, so the reachable
figures are enumerable and spoken forms are a lookup table
(data/spoken-forms.yaml), native-verified once per language. Anything not in
the table is left as digits - a safe fallback TTS reads acceptably, which by
construction should not occur.

Accepts ValidatedSentence ONLY. That is the ordering guarantee: text that has
not passed guardrails cannot be verbalised, and text that has not been
verbalised through here cannot become SpeakableText.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .figures import extract_figures, normalise_digits
from .schemas import FigureKind, SpeakableText, ValidatedSentence

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

_AED_PREFIX = "AED "


@dataclass(frozen=True)
class SpokenForms:
    # (language, kind, canonical value) -> spoken form
    by_value: dict[tuple[str, FigureKind, float], str]
    # (language, exact surface) -> spoken form, e.g. ("en", "Q4 2026")
    by_surface: dict[tuple[str, str], str]


def load_spoken_forms(path: Path | None = None) -> SpokenForms:
    raw = yaml.safe_load(
        (path or _DATA_DIR / "spoken-forms.yaml").read_text(encoding="utf-8")
    )
    by_value: dict[tuple[str, FigureKind, float], str] = {}
    by_surface: dict[tuple[str, str], str] = {}
    for language, entries in (raw or {}).items():
        for entry in entries or []:
            if "surface" in entry:
                by_surface[(language, entry["surface"])] = entry["spoken"]
            else:
                by_value[(language, entry["kind"], float(entry["value"]))] = entry[
                    "spoken"
                ]
    return SpokenForms(by_value=by_value, by_surface=by_surface)


def verbalise(sentence: ValidatedSentence, forms: SpokenForms) -> SpeakableText:
    if not isinstance(sentence, ValidatedSentence):
        raise TypeError(
            "verbalise() accepts only ValidatedSentence - text that has not "
            "passed guardrails must never be verbalised (AGENTS.md invariant 4)"
        )
    text = normalise_digits(sentence.text)

    # Surface-keyed replacements first (e.g. "Q4 2026"), so their component
    # numbers are gone before the numeric pass.
    for (language, surface), spoken in forms.by_surface.items():
        if language == sentence.language and surface in text:
            text = text.replace(surface, spoken)

    # Numeric replacements, right to left so earlier spans stay valid.
    for match in sorted(extract_figures(text), key=lambda m: m.start, reverse=True):
        spoken = forms.by_value.get(
            (sentence.language, match.figure.kind, match.figure.value)
        )
        if spoken is None:
            continue  # not in the table: leave digits, TTS reads them
        start = match.start
        # Spoken forms for amounts name the currency, so consume an "AED "
        # prefix rather than speaking it twice.
        if match.figure.kind == "amount" and text[:start].endswith(_AED_PREFIX):
            start -= len(_AED_PREFIX)
        text = text[:start] + spoken + text[match.end :]

    return SpeakableText(text=text, language=sentence.language)
