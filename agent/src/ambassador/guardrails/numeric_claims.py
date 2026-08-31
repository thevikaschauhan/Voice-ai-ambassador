"""Numeric-claims validator: every figure in generated text must exist in the
allowed set (docs/03-).

Operates on digits, which is why it is language-agnostic and why it must run
before verbalisation (which destroys digits). Extraction lives in
ambassador.figures and covers western, Arabic-Indic and Devanagari digits.

Two things the extractor tells this module that it used to discard, both of
which were live bypasses:

  Whether the figure is MONEY. A currency token beside a figure means the
  sentence is claiming a price, so it is checked against the currency amounts
  alone. `AllowedFigures.amounts` also holds square footages and Binghatti's
  hotline number, and while a number is a number when you are asking whether
  it was invented, a PRICE validating against a phone number is not that
  question. "It starts at AED 80015" and "It starts at AED 380" were both
  spoken; the only source of 80015 is an identifier and of 380 a square
  footage.

  Whether the surface is composed ARITHMETIC. "8 × 10^5" is three integers
  that are each individually exempt and together state 800,000. It is blocked
  on its face rather than computed - the system does no arithmetic (AGENTS.md
  invariant 2), least of all the model's.
"""

from ..figures import Numerals, extract_figures, find_composed_arithmetic
from ..schemas import AllowedFigures, ExtractedFigure


def check_numeric_claims(
    text: str, allowed: AllowedFigures, numerals: Numerals | None = None
) -> list[ExtractedFigure]:
    """Return the figures that violate the allowed set. Empty list = pass.

    Policy (docs/03-): amounts, percents and years must be in the allowed set;
    integers 0-12 are exempt as conversational counts. That exemption is a
    deliberate, documented hole, and it is void the moment a currency token
    sits beside the figure - the claim that a small integer "cannot state a
    price" was disproved by "It starts at AED 12", which the sentence itself
    prices in dirhams. The same rule voids the year classification, so
    "It starts at AED 2026" is a price to check, not an allowed handover year.
    """
    violations: list[ExtractedFigure] = []
    for match in extract_figures(text, numerals):
        fig = match.figure
        if fig.kind == "count":
            continue
        if fig.kind == "amount":
            # Money is checked against money. A non-currency amount keeps the
            # untyped set: a bare quantity or the hotline read on its own is a
            # real thing the agent says (ADR-008 leaves per-project scoping to
            # the next tier, and this is only the kind split).
            pool = allowed.currency_amounts if match.currency else allowed.amounts
            if fig.value in pool:
                continue
        elif fig.kind == "percent" and fig.value in allowed.percents:
            continue
        elif fig.kind == "year" and int(fig.value) in allowed.years:
            continue
        violations.append(fig)
    # Reported last so the ordinary per-figure violations lead the detail line,
    # which is what an operator reading the log is looking for.
    violations.extend(match.figure for match in find_composed_arithmetic(text, numerals))
    return violations
