"""Figure extraction and normalisation, shared by the numeric-claims validator
and verbalisation.

Handles western, Arabic-Indic and Devanagari digits, Arabic numeric
separators, and the multiplier words buyers and models actually use
(k, thousand, million, lakh, crore). Normalisation reduces every surface form
to one canonical value: 975,000 / 975000 / 975k / 0.975 million / ٩٧٥٬٠٠٠
all become 975000.0. (And 24 lakh is 2,400,000 while 2.4 crore is
24,000,000 - a 10x difference this module exists to keep straight.)
"""

import re
from dataclasses import dataclass

from .schemas import ExtractedFigure, FigureKind

# Digits: Arabic-Indic (٠), extended Arabic-Indic (۰), Devanagari (०) -> ASCII.
_DIGIT_MAP = {}
for _block_zero in (0x0660, 0x06F0, 0x0966):
    for _d in range(10):
        _DIGIT_MAP[chr(_block_zero + _d)] = str(_d)
_DIGIT_MAP["٬"] = ","  # Arabic thousands separator
_DIGIT_MAP["٫"] = "."  # Arabic decimal separator
_TRANSLATION = str.maketrans(_DIGIT_MAP)

_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "lakh": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}

_NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<mult>thousand|million|lakh|lacs|lac|crores|crore|k|m)\b)?"
    r"(?:\s*(?P<pct>%|percent\b|per\s+cent\b))?",
    re.IGNORECASE,
)


def normalise_digits(text: str) -> str:
    return text.translate(_TRANSLATION)


@dataclass(frozen=True)
class FigureMatch:
    figure: ExtractedFigure
    start: int
    end: int


def _classify(
    num_surface: str, value: float, mult: str | None, pct: str | None
) -> FigureKind:
    if pct:
        return "percent"
    if (
        mult is None
        and "." not in num_surface
        and "," not in num_surface
        and value == int(value)
        and 1900 <= value <= 2099
        and len(num_surface) == 4
    ):
        return "year"
    if mult is None and value == int(value) and 0 <= value <= 12:
        return "count"
    return "amount"


def extract_figures(text: str) -> list[FigureMatch]:
    """Every figure in the text, with spans, from digit-normalised text."""
    text = normalise_digits(text)
    matches: list[FigureMatch] = []
    for m in _NUMBER_RE.finditer(text):
        num_surface = m.group("num")
        value = float(num_surface.replace(",", ""))
        mult = m.group("mult")
        if mult:
            value *= _MULTIPLIERS[mult.lower()]
        kind = _classify(num_surface, value, mult, m.group("pct"))
        matches.append(
            FigureMatch(
                figure=ExtractedFigure(
                    surface=m.group(0).strip(), value=value, kind=kind
                ),
                start=m.start(),
                end=m.end(),
            )
        )
    return matches
