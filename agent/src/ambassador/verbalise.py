"""Closed-set verbalisation (ADR-009).

Only figures in the allowed set can reach this module, so the reachable
figures are enumerable and spoken forms are a lookup table
(data/spoken-forms.yaml), native-verified once per language. Anything not in
the table is left as digits - a safe fallback TTS reads acceptably, which by
construction should not occur.

The same file carries each language's `currency_tokens`, because a spoken form
names the currency in its own language ("... dirhams") and the written token it
has to swallow is therefore language-specific too. Holding the token list in
code as the Latin "AED" would put the double-currency bug back the day ar and
hi are natively authored.

Accepts ValidatedSentence ONLY. That is the ordering guarantee: text that has
not passed guardrails cannot be verbalised, and text that has not been
verbalised through here cannot become SpeakableText.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .figures import extract_figures, normalise_digits
from .schemas import AllowedFigures, FigureKind, SpeakableText, ValidatedSentence

# The one language this build team self-certifies (AGENTS.md), and so the
# reference for what a complete table looks like.
_REFERENCE_LANGUAGE = "en"

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# A character that would make a currency token the head or the tail of a longer
# word, so a token touching one is prose rather than a currency marker.
#
# ASCII-only, and `re.ASCII` on the compiled patterns is what makes it so. That
# flag is load-bearing twice over. Python's default \w counts Arabic and
# Devanagari letters as word characters, so under the default a token written
# flush against Arabic script had no boundary beside it, was left alone, and the
# double-currency bug returned in exactly the two languages this team cannot
# self-certify. The flag also keeps case-insensitive matching to ASCII folding,
# which is all these tokens need.
#
# The apostrophes are here because a word boundary is not enough on its own:
# "Sa'aed 985,000" ends in the letters "aed" with an apostrophe in front, so a
# boundary existed, the token matched, and the replacement ate the name down to
# "Sa'". Both the straight and the typographic apostrophe, spelled as an escape
# so this file stays ASCII.
_ADJOINING = r"[\w'\u2019]"

# The gap between the token and the digits: spaces and tabs, never a line
# break. \s* spanned newlines, so "It costs 985,000\nAED conversion aside" lost
# an AED that belonged to the next clause. \Z and \A rather than $ and ^ for the
# same reason - $ also matches in front of a trailing newline.
_GAP = r"[ \t]*"


@dataclass(frozen=True)
class CurrencyPatterns:
    """Where a currency token may sit relative to an amount, in one language.

    Spoken forms for amounts already name the currency ("... dirhams"), so a
    currency token written next to the digits has to be swallowed with them or
    it is spoken twice. Both orders occur live: the prompt asks for plain digits
    and the model writes "AED 985,000" on some turns and "985,000 AED" on
    others - the suffix form is what produced "nine hundred and eighty-five
    thousand dirhams AED".

    The separating space may be absent in either direction. That is true of the
    prefix only since figures.py stopped refusing to start a match after a
    letter (commit 8937557): before it, "AED985,000" extracted no figure at all,
    so nothing reached this code to consume.
    """

    before: re.Pattern[str]
    after: re.Pattern[str]


@dataclass(frozen=True)
class SpokenForms:
    # (language, kind, canonical value) -> spoken form
    by_value: dict[tuple[str, FigureKind, float], str]
    # (language, exact surface) -> spoken form, e.g. ("en", "Q4 2026")
    by_surface: dict[tuple[str, str], str]
    # language -> its currency tokens, compiled. Absent for a language whose
    # token list is empty, which is ar and hi until their spoken forms are
    # natively authored: the tokens to consume depend on what the spoken form
    # says, so they are per-language data like everything else here.
    currency: dict[str, CurrencyPatterns]


def _currency_patterns(tokens: Sequence[str]) -> CurrencyPatterns | None:
    """Compile one language's currency tokens into a prefix and suffix pattern.

    Done once at load rather than per amount. Longest alternative first so
    "dirhams" is not matched as "dirham" with a stray "s" left to be spoken, and
    deduplicated case-insensitively because the match is.
    """
    unique: dict[str, str] = {}
    for token in tokens:
        unique.setdefault(token.lower(), token)
    if not unique:
        return None
    alternatives = "|".join(
        re.escape(token) for token in sorted(unique.values(), key=len, reverse=True)
    )
    flags = re.IGNORECASE | re.ASCII
    return CurrencyPatterns(
        before=re.compile(rf"(?<!{_ADJOINING})(?:{alternatives}){_GAP}\Z", flags),
        after=re.compile(rf"\A{_GAP}(?:{alternatives})(?!{_ADJOINING})", flags),
    )


def load_spoken_forms(path: Path | None = None) -> SpokenForms:
    source = path or _DATA_DIR / "spoken-forms.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    by_value: dict[tuple[str, FigureKind, float], str] = {}
    by_surface: dict[tuple[str, str], str] = {}
    currency: dict[str, CurrencyPatterns] = {}
    for language, block in (raw or {}).items():
        block = _language_block(block, language, source)
        for entry in block.get("forms") or []:
            spoken = _entry_spoken(entry, language, source)
            if spoken is None:
                continue  # a placeholder row: owed, not authored
            if "surface" in entry:
                by_surface[(language, entry["surface"])] = spoken
            else:
                by_value[(language, entry["kind"], float(entry["value"]))] = spoken
        patterns = _currency_patterns(block.get("currency_tokens") or [])
        if patterns is not None:
            currency[language] = patterns
    return SpokenForms(by_value=by_value, by_surface=by_surface, currency=currency)


def _entry_spoken(entry: dict[str, Any], language: str, source: Path) -> str | None:
    """One entry's spoken form, or None when the row is a placeholder.

    A row may be written with no `spoken` value, to record that a form is OWED
    and to say who owes it. The hotline is the case that needed it: how a
    Binghatti number should be read aloud is a client fact, not something this
    team may author (#10), and without a row the only trace of that would be a
    comment in a file nobody re-reads. A placeholder row carries `verify:`
    explaining what is missing - the same discipline `data/whitelist.yaml`
    applies to its `why`, and it is required rather than optional because an
    unexplained blank reads as an editing accident.

    **Which way this fails: towards the digit fallback.** A placeholder
    contributes no form, so verbalisation leaves the digits exactly as it does
    today - the gap stays visible and nothing new is spoken. The opposite
    direction is the one that matters: loading placeholder text as a spoken
    form would put it into a buyer's ear. That is why an empty or non-string
    `spoken` is REJECTED here rather than quietly treated as absent - a typo
    that empties a real form must fail in front of whoever made it, not
    silently downgrade a language that used to speak.
    """
    if "spoken" in entry and entry["spoken"] is not None:
        spoken = entry["spoken"]
        if not isinstance(spoken, str) or not spoken.strip():
            raise ValueError(
                f"{source.name}: the {language!r} spoken form for "
                f"{entry.get('surface') or entry.get('value')!r} is empty or "
                "not text. It is substituted into speech verbatim, so it has "
                "to be a non-empty quoted string - and a row that is not yet "
                "authored is written with no 'spoken' value and a 'verify:' "
                "note, not with an empty one."
            )
        return spoken
    if not str(entry.get("verify") or "").strip():
        raise ValueError(
            f"{source.name}: the {language!r} entry for "
            f"{entry.get('surface') or entry.get('value')!r} has no spoken "
            "form and no 'verify:' note. A row with no form is a claim that "
            "one is owed, and it has to say by whom and why or it reads as a "
            "line somebody half-deleted."
        )
    return None


def _language_block(block: Any, language: str, source: Path) -> dict[str, Any]:
    """Each language maps to `currency_tokens` and `forms`, or the load fails.

    The file used to map a language straight to its list of forms. A file still
    in that shape would reach `.get` on a list and raise an AttributeError from
    inside the loader, which is not a message anyone can act on.
    """
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ValueError(
            f"{source.name}: {language!r} must map to 'currency_tokens' and "
            f"'forms', got {type(block).__name__}."
        )
    return block


def _consume_currency(
    text: str, start: int, end: int, patterns: CurrencyPatterns | None
) -> tuple[int, int]:
    """Widen an amount's span over an adjacent currency token, either side.

    Pure span arithmetic on already-normalised text: it decides what the
    replacement covers, it never rewrites anything itself.
    """
    if patterns is None:
        return start, end
    before = patterns.before.search(text[:start])
    if before is not None:
        start = before.start()
    after = patterns.after.search(text[end:])
    if after is not None:
        end += after.end()
    return start, end


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
        start, end = match.start, match.end
        if match.figure.kind == "amount":
            start, end = _consume_currency(
                text, start, end, forms.currency.get(sentence.language)
            )
        text = text[:start] + spoken + text[end:]

    return SpeakableText(text=text, language=sentence.language)


def spoken_form_gaps(
    forms: SpokenForms, allowed: AllowedFigures, language: str
) -> dict[FigureKind, list[float]]:
    """Allowed figures with no spoken form in this language, by kind.

    ADR-009's claim is that the table is COMPLETE by construction: only allowed
    figures can reach verbalisation, so the reachable figures are enumerable
    and can all be authored once. Nothing enforced that, and it was not true -
    seven allowed amounts had no English form and fell through to the digit
    fallback.

    The fallback is safe for a quantity and wrong for anything read as a
    sequence. `80015` is Binghatti's hotline, spoken when routing to a human,
    and TTS reads it as eighty thousand and fifteen. That is on the escalation
    path, which AGENTS.md says gets the same polish as the happy path.

    Years are excluded deliberately: they are spoken as digits correctly in
    every language here, and quarters, which are not, are surface-keyed rather
    than value-keyed and are checked separately by `quarter_surface_gaps`.

    Checked against `currency_amounts`, not `amounts`. `amounts` also holds
    square footages and the hotline number, and those SHOULD have no form: the
    digit fallback reads a bare quantity correctly, while a currency-naming
    form on one of them would make the buyer hear "four hundred and twenty
    dirhams square feet". Reporting them as gaps would send whoever authors
    the table straight into that.
    """
    gaps: dict[FigureKind, list[float]] = {}
    for kind, values in (
        ("amount", allowed.currency_amounts),
        ("percent", allowed.percents),
    ):
        missing = sorted(
            value
            for value in values
            if (language, kind, float(value)) not in forms.by_value
        )
        if missing:
            gaps[kind] = missing  # type: ignore[index]
    return gaps


def quarter_surface_gaps(forms: SpokenForms, language: str) -> list[str]:
    """Quarter surfaces authored in one language and missing in another.

    Quarters have no enumerable source the way figures do - they come from
    inventory handover dates as text - so completeness is defined against the
    languages that DO have them rather than against a value set. English is
    the reference because it is the language this team may author.
    """
    reference = {
        surface for (lang, surface) in forms.by_surface if lang == _REFERENCE_LANGUAGE
    }
    present = {surface for (lang, surface) in forms.by_surface if lang == language}
    return sorted(reference - present)


def identifier_gaps(
    forms: SpokenForms, allowed: AllowedFigures, language: str
) -> list[float]:
    """Allowed identifiers with no spoken form in this language.

    Reported separately from `spoken_form_gaps` because the ASK is different,
    not because the mechanism is. That function deliberately checks
    `currency_amounts`, since a currency-naming form on a square footage makes
    the buyer hear "four hundred and twenty dirhams square feet" - and the
    hotline was swept out of the packet with the square footages, because both
    are non-currency `amounts`. They are not the same case. A square footage
    with no form is correct: the digit fallback reads a bare quantity
    correctly. An identifier with no form is the defect: 80015 is read as
    "eighty thousand and fifteen", on the escalation path.

    So the packet asks for identifiers, and asks differently - a sequence of
    digits, naming no currency.

    The lookup is keyed on `"amount"`, which is not a typo. `verbalise()` looks
    a form up by the kind the EXTRACTOR assigned, and `figures._classify` calls
    a bare five-digit integer an amount; there is no "identifier" FigureKind
    and inventing a key here would mean an authored hotline form sat in the
    file and was never spoken. That is the class of defect this repository
    keeps finding, so the gap check and the runtime lookup deliberately use one
    key.
    """
    return sorted(
        value
        for value in allowed.identifiers
        if (language, "amount", float(value)) not in forms.by_value
    )
