"""Figure extraction and normalisation, shared by the numeric-claims validator
and verbalisation.

Handles western, Arabic-Indic and Devanagari digits, Arabic numeric
separators, and the multiplier words buyers and models actually use
(k, thousand, million, lakh, crore). Normalisation reduces every surface form
to one canonical value: 975,000 / 975000 / 975k / 0.975 million / ٩٧٥٬٠٠٠
all become 975000.0. (And 24 lakh is 2,400,000 while 2.4 crore is
24,000,000 - a 10x difference this module exists to keep straight.)

## The one rule this module is built around

Over-extraction and over-blocking are the SAFE direction. An over-extracted
figure blocks a sentence, which is recoverable; an under-extracted one SPEAKS
an unverified figure, which is the failure class the product exists to
prevent. Every judgement call below resolves that way, and the module has a
history of bypasses that were all under-extraction:

  `AED750,000` extracted nothing at all because a letter blocked the match.
  `AED1,985,000` restarted after the comma and extracted the embedded, allowed
  `985,000`, so a fabricated price validated as a real one.
  `AED 380<U+202F>000` split into an allowed square footage of 380 and an
  exempt 000 - the same class again, one separator later.
  `AED 12`, `AED 2026` and `AED -985,000` were a count, a year and an allowed
  positive amount, because classification could not see the currency or the
  sign.
  `8 × 10^5` was three exempt small integers composing to 800,000.

## Where the vocabulary lives

The digits are code; the WORDS are data (`data/numerals.yaml`), because the
magnitude and the kind often live in the token beside the digits and that
token is a native word nobody here may author for Arabic or Hindi. The file
carries `VERIFY:` markers for those, and the gap is disclosed in
docs/03-guardrails.md rather than absorbed.

Loading is I/O and everything that decides anything here is not: `load_numerals()`
reads and compiles the file once, every function below takes the compiled
`Numerals` as an argument, and the module-level default exists only so the
three existing call sites keep their signatures. Pass an explicit `Numerals`
in tests - that is how the Arabic and Hindi mechanism is proved without
authoring the data.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .schemas import ExtractedFigure, FigureKind

# Repo layout: <repo>/agent/src/ambassador/figures.py -> <repo>/data
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Digits: Arabic-Indic (٠), extended Arabic-Indic (۰), Devanagari (०) -> ASCII.
# One character to one, always: verbalisation replaces text by the spans this
# module returns, so a normalisation that changed the length would corrupt them.
_DIGIT_MAP = {}
for _block_zero in (0x0660, 0x06F0, 0x0966):
    for _d in range(10):
        _DIGIT_MAP[chr(_block_zero + _d)] = str(_d)
_DIGIT_MAP["٬"] = ","  # Arabic thousands separator
_DIGIT_MAP["٫"] = "."  # Arabic decimal separator
_TRANSLATION = str.maketrans(_DIGIT_MAP)

# A leading minus is part of the figure. ASCII hyphen-minus and U+2212 MINUS
# SIGN only: the en-dash and the ASCII hyphen between two numbers are range
# marks ("985,000-1,200,000", "2026-2027"), and the lookbehind below keeps
# those out. A leading PLUS is deliberately not a sign - it does not change the
# value, so matching it would only change the surface.
_SIGNS = "-−"


def normalise_digits(text: str) -> str:
    return text.translate(_TRANSLATION)


@dataclass(frozen=True)
class Numerals:
    """The compiled numeric vocabulary. Built by `load_numerals()`."""

    multipliers: dict[str, float]
    number_re: re.Pattern
    # Applied to the text BEFORE a figure and AFTER it, anchored, so a currency
    # token on either side is found without scanning the whole sentence.
    currency_before: re.Pattern
    currency_after: re.Pattern
    arithmetic_re: re.Pattern
    # Removes group separators from a matched surface before float().
    strip_separators: dict[int, int | None]
    # Provenance: the languages someone competent has actually written words
    # for. Reported by `languages_covered()`; nothing routes on it.
    languages: frozenset[str]


def _alternation(*groups: list[str]) -> str:
    """Longest first, or "lakhs" matches as "lakh" and then fails the word
    boundary - which is how "24 lakhs" once extracted a bare 24. An internal
    space becomes `\\s+` so "per cent" matches however it is spaced.

    Groups are concatenated in the order given, so a caller can put symbols
    before words. A symbol NEVER takes a word boundary: `%\\b` fails at the end
    of "20%", which silently turned every trailing percentage into a bare
    amount.
    """
    parts: list[str] = []
    for group in groups:
        for word in sorted(group, key=len, reverse=True):
            parts.append(re.escape(word).replace(r"\ ", r"\s+"))
    return "|".join(parts)


def _require_str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list, got {type(value).__name__}.")
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                f"{where} must hold quoted strings, got {item!r} "
                f"({type(item).__name__}). YAML reads bare y/n/on/off/true as "
                "booleans, so quote every word."
            )
    return value


def load_numerals(path: Path | None = None) -> Numerals:
    """Read and compile data/numerals.yaml, or say what is wrong with it.

    The next person to edit that file is an engineer transcribing a native
    reviewer's word list, so every failure names the file and the key.
    """
    source = path or _DATA_DIR / "numerals.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source.name}: the file must be a mapping with 'symbols', "
            f"'latin_currency_tokens' and 'languages', got "
            f"{type(raw).__name__}."
        )

    symbols = raw.get("symbols")
    if not isinstance(symbols, dict):
        raise ValueError(f"{source.name}: 'symbols' must be a mapping.")
    currency_symbols = _require_str_list(
        symbols.get("currency"), f"{source.name}: symbols.currency"
    )
    percent_symbols = _require_str_list(
        symbols.get("percent"), f"{source.name}: symbols.percent"
    )
    arithmetic = _require_str_list(
        symbols.get("arithmetic"), f"{source.name}: symbols.arithmetic"
    )
    separators = _require_str_list(
        symbols.get("group_separators"), f"{source.name}: symbols.group_separators"
    )
    if any(len(sep) != 1 for sep in separators):
        raise ValueError(
            f"{source.name}: every group separator must be exactly one "
            "character - it goes inside a character class, and it must not "
            "change the length of the text when stripped."
        )
    latin_currency = _require_str_list(
        raw.get("latin_currency_tokens"), f"{source.name}: latin_currency_tokens"
    )

    languages = raw.get("languages")
    if not isinstance(languages, dict):
        raise ValueError(f"{source.name}: 'languages' must be a mapping.")

    multipliers: dict[str, float] = {}
    percent_words: list[str] = []
    currency_words: list[str] = list(latin_currency)
    covered: set[str] = set()
    for language, block in languages.items():
        where = f"{source.name}: languages.{language}"
        if not isinstance(block, dict):
            raise ValueError(f"{where} must be a mapping.")
        table = block.get("multipliers") or {}
        if not isinstance(table, dict):
            raise ValueError(f"{where}.multipliers must be a mapping.")
        for word, factor in table.items():
            if not isinstance(word, str):
                raise ValueError(
                    f"{where}.multipliers has the non-string key {word!r}. "
                    "YAML reads bare y/n/on/off/true as booleans, so quote "
                    "every word."
                )
            if isinstance(factor, bool) or not isinstance(factor, int | float):
                raise ValueError(
                    f"{where}.multipliers[{word!r}] must be a number, got "
                    f"{factor!r}."
                )
            multipliers[word.strip().lower()] = float(factor)
        words = _require_str_list(block.get("percent_words"), f"{where}.percent_words")
        percent_words.extend(words)
        native_currency = _require_str_list(
            block.get("currency_words"), f"{where}.currency_words"
        )
        currency_words.extend(native_currency)
        if table or words or native_currency:
            covered.add(language)

    if not multipliers:
        raise ValueError(
            f"{source.name}: no multiplier words at all. A file with an empty "
            "table reads every '8 million' as a count of 8."
        )
    # Every alternation below must have at least one alternative. An empty one
    # matches the EMPTY STRING, and an empty match is not the safe direction
    # here: an empty percent alternative makes every figure a percentage, and
    # an empty currency alternative makes every figure money. Both scramble the
    # classification rather than merely widening it, so they fail at start-up.
    percent_alternatives = [
        part
        for part in (
            _alternation(percent_symbols),
            rf"(?:{_alternation(percent_words)})(?!\w)" if percent_words else "",
        )
        if part
    ]
    if not percent_alternatives:
        raise ValueError(
            f"{source.name}: no percent symbols or words at all. With none, "
            "'20%' is a bare amount and the percentage policy checks nothing."
        )
    if not currency_words and not currency_symbols:
        raise ValueError(
            f"{source.name}: no currency tokens at all. With none, a price "
            "keeps the small-integer and year exemptions and 'AED 12' is a "
            "conversational count again."
        )

    sep_class = "".join(re.escape(c) for c in separators)
    number_re = re.compile(
        # A sign, or nothing - and the two cases need DIFFERENT lookbehinds.
        # The signed branch refuses a preceding word character so "ADR-011" and
        # "985,000-1,200,000" stay ranges rather than becoming negatives. The
        # unsigned branch blocks only digits, commas and decimal points: a
        # letter must never block a match, because with letters blocked
        # "AED750,000" extracted nothing and went unchecked.
        rf"(?:(?<![\w.,])(?P<sign>[{re.escape(_SIGNS)}])(?=[\d.])|(?<![\d.,]))"
        # Group separators must be followed by more digits, so a no-break space
        # between "3" and "bedrooms" is not swallowed into the surface.
        rf"(?P<num>\d+(?:[,{sep_class}]\d+)*(?:\.\d+)?|\.\d+)"
        # Exponent: "8e5" is ONE numeric literal meaning 800,000, so it is
        # normalised. A caret ("10^5") is composed arithmetic and is blocked
        # instead - see `find_composed_arithmetic`.
        r"(?P<exp>[eE][-+−]?\d+)?"
        # A hyphen may join the multiplier: the model writes "8-million".
        # `(?!\w)` and not `\b`: a word may END in a combining mark, and
        # Devanagari "करोड़" does - the nukta is not a word character, so `\b`
        # after it never held and the crore claim stayed an exempt count.
        rf"(?:[-\s]*(?P<mult>{_alternation(list(multipliers))})(?!\w))?"
        # Symbols first and without a word boundary; a spelled-out word takes
        # one so "20 percentage" is not a percentage.
        rf"(?:\s*(?P<pct>{'|'.join(percent_alternatives)}))?",
        re.IGNORECASE,
    )

    currency_alternation = _alternation(currency_words, currency_symbols)
    return Numerals(
        multipliers=multipliers,
        number_re=number_re,
        # A word token must not be part of a longer word: "PAED" is not AED,
        # and "12 dhow" is not 12 dirhams.
        currency_before=re.compile(rf"(?<!\w)(?:{currency_alternation})\s*$", re.I),
        currency_after=re.compile(rf"^\s*(?:{currency_alternation})(?!\w)", re.I),
        arithmetic_re=re.compile(
            "[" + "".join(re.escape(c) for c in arithmetic) + "]"
        ),
        strip_separators=str.maketrans("", "", "," + "".join(separators)),
        languages=frozenset(covered),
    )


@lru_cache(maxsize=1)
def default_numerals() -> Numerals:
    """The shipped vocabulary, read once. Everything that DECIDES anything
    takes a `Numerals` explicitly; this is only the default for the call sites
    that predate the file."""
    return load_numerals()


def languages_covered(numerals: Numerals | None = None) -> frozenset[str]:
    """The languages someone competent has actually written numeric words for.

    Today that is English alone, so a magnitude or percent word in pure Arabic
    or Devanagari script is not read and the figure keeps its small-integer
    exemption. Disclosed in docs/03-guardrails.md; the same shape as
    `prohibited.languages_covered()`, and for the same reason - a system that
    looks equally protected in every language it offers is the worse failure.
    """
    return (numerals or default_numerals()).languages


@dataclass(frozen=True)
class FigureMatch:
    figure: ExtractedFigure
    start: int
    end: int
    # A currency token sits beside this figure, so it is money. Money is never
    # an exempt count and never a year, and it is checked against the currency
    # amounts alone - a price may not validate against a square footage or the
    # hotline number.
    currency: bool = False
    # This surface is figures joined by an arithmetic operator. It has no single
    # value, so it is a violation on its face - see `find_composed_arithmetic`.
    composed: bool = False


def _currency_adjacent(text: str, start: int, end: int, numerals: Numerals) -> bool:
    return (
        numerals.currency_before.search(text[:start]) is not None
        or numerals.currency_after.search(text[end:]) is not None
    )


def _classify(
    num_surface: str,
    value: float,
    mult: str | None,
    pct: str | None,
    exp: str | None,
    currency: bool,
) -> FigureKind:
    if pct:
        return "percent"
    if currency:
        # A currency token beside the figure means it claims to be money, and
        # money is checked, never exempted. This is what stops "It starts at
        # AED 12" being a conversational count and "It starts at AED 2026"
        # being an allowed handover year.
        return "amount"
    plain = num_surface.replace(",", "")
    if (
        mult is None
        and exp is None
        and "." not in num_surface
        and plain == num_surface
        and value == int(value)
        and 1900 <= value <= 2099
        and len(num_surface) == 4
    ):
        return "year"
    if mult is None and exp is None and value == int(value) and 0 <= value <= 12:
        return "count"
    return "amount"


def extract_figures(text: str, numerals: Numerals | None = None) -> list[FigureMatch]:
    """Every figure in the text, with spans, from digit-normalised text."""
    nm = numerals or default_numerals()
    text = normalise_digits(text)
    matches: list[FigureMatch] = []
    for m in nm.number_re.finditer(text):
        num_surface = m.group("num")
        digits = num_surface.translate(nm.strip_separators)
        exp = m.group("exp")
        value = float(digits + exp.replace("−", "-")) if exp else float(digits)
        mult = m.group("mult")
        if mult:
            value *= nm.multipliers[re.sub(r"\s+", " ", mult.strip().lower())]
        if m.group("sign"):
            value = -value
        currency = _currency_adjacent(text, m.start(), m.end(), nm)
        kind = _classify(num_surface, value, mult, m.group("pct"), exp, currency)
        matches.append(
            FigureMatch(
                figure=ExtractedFigure(
                    surface=m.group(0).strip(), value=value, kind=kind
                ),
                start=m.start(),
                end=m.end(),
                currency=currency,
            )
        )
    return matches


def _figure_ending_before(
    figures: list[FigureMatch], text: str, pos: int
) -> int | None:
    """Index of the figure that ends at `pos`, whitespace aside."""
    for index, figure in enumerate(figures):
        if figure.end <= pos and not text[figure.end : pos].strip():
            return index
    return None


def _figure_starting_after(
    figures: list[FigureMatch], text: str, pos: int
) -> int | None:
    """Index of the figure that starts at `pos`, whitespace aside."""
    for index, figure in enumerate(figures):
        if figure.start >= pos and not text[pos : figure.start].strip():
            return index
    return None


def find_composed_arithmetic(
    text: str, numerals: Numerals | None = None
) -> list[FigureMatch]:
    """Figures joined by an arithmetic operator, one match per composed run.

    These are BLOCKED, never computed. AGENTS.md invariant 2 says the system
    does no arithmetic; doing the model's silently would be worse than
    refusing it. "It starts at AED 8 × 10^5" is three integers that are each
    individually exempt and together say 800,000, so every token passed the
    validator while the sentence stated a price in no record. The prompt
    already forbids this syntax, so blocking it costs nothing real.

    The returned figure carries `composed=True`, and that - not its value - is
    what makes it a violation. `value` is the LEADING OPERAND, kept only so a
    violation report carries a real number alongside the surface; the surface
    as a whole has no single value, which is the point.
    """
    nm = numerals or default_numerals()
    text = normalise_digits(text)
    figures = extract_figures(text, nm)
    if not figures:
        return []

    runs: list[tuple[int, int, set[int]]] = []
    for token in nm.arithmetic_re.finditer(text):
        touching = {
            index
            for index in (
                _figure_ending_before(figures, text, token.start()),
                _figure_starting_after(figures, text, token.end()),
            )
            if index is not None
        }
        if not touching:
            continue  # a stray operator in prose, joined to no figure
        start = min([figures[i].start for i in touching] + [token.start()])
        end = max([figures[i].end for i in touching] + [token.end()])
        if runs and start <= runs[-1][1]:
            previous = runs[-1]
            runs[-1] = (
                min(previous[0], start),
                max(previous[1], end),
                previous[2] | touching,
            )
        else:
            runs.append((start, end, touching))

    composed: list[FigureMatch] = []
    for start, end, touching in runs:
        leading = figures[min(touching)]
        composed.append(
            FigureMatch(
                figure=ExtractedFigure(
                    surface=text[start:end].strip(),
                    value=leading.figure.value,
                    kind="amount",
                ),
                start=start,
                end=end,
                currency=any(figures[i].currency for i in touching),
                composed=True,
            )
        )
    return composed
