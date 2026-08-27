"""Numeric-claims validator: every figure in generated text must exist in the
allowed set (docs/03-).

Operates on digits, which is why it is language-agnostic and why it must run
before verbalisation (which destroys digits). Extraction lives in
ambassador.figures and covers western, Arabic-Indic and Devanagari digits.
"""

from ..figures import extract_figures
from ..schemas import AllowedFigures, ExtractedFigure


def check_numeric_claims(text: str, allowed: AllowedFigures) -> list[ExtractedFigure]:
    """Return the figures that violate the allowed set. Empty list = pass.

    Policy (docs/03-): amounts, percents and years must be in the allowed set;
    integers 0-12 are exempt as conversational counts - a deliberate,
    documented hole that cannot state a price or a year.
    """
    violations: list[ExtractedFigure] = []
    for match in extract_figures(text):
        fig = match.figure
        if fig.kind == "count":
            continue
        if fig.kind == "amount" and fig.value in allowed.amounts:
            continue
        if fig.kind == "percent" and fig.value in allowed.percents:
            continue
        if fig.kind == "year" and int(fig.value) in allowed.years:
            continue
        violations.append(fig)
    return violations
