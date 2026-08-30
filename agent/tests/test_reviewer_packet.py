"""The native-reviewer packet generator.

Three issues (#4, #14, #15) wait on one person's time, so the packet has to be
right the first time - a second session to collect what the first one missed
costs another calendar week.

The property that matters is COMPLETENESS: the packet is generated from the
same loaders the runtime uses, so it asks for exactly what the system will
demand back. These tests are what notices when it stops doing that.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
TOOL = AGENT_DIR / "tools" / "reviewer_packet.py"

pytest.importorskip("yaml")


def generate(language: str) -> str:
    result = subprocess.run(
        [sys.executable, str(TOOL), language],
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def arabic() -> str:
    return generate("ar")


def test_it_asks_for_every_currency_amount_the_verbaliser_will_want(arabic):
    """Derived from inventory, not typed out.

    A hand-written packet goes stale the moment a project is added: a new
    price plus four derived instalments would simply be absent, and nobody
    would find out until a buyer heard raw digits in Arabic.
    """
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from ambassador.inventory import build_allowed_figures, load_inventory

    allowed = build_allowed_figures(load_inventory())
    for value in allowed.currency_amounts:
        assert f"- [ ] AED {int(value):,} ->" in arabic, value


def test_it_does_not_ask_for_a_form_for_a_square_footage_or_the_hotline(arabic):
    """The trap this packet exists to avoid walking a reviewer into.

    Those values are in `amounts` but are not money. A currency-naming form on
    one of them makes the buyer hear "four hundred and twenty dirhams square
    feet", and the reviewer would have no way to know.
    """
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from ambassador.inventory import build_allowed_figures, load_inventory

    allowed = build_allowed_figures(load_inventory())
    for value in allowed.amounts - allowed.currency_amounts:
        # A whole line item, not a substring: "AED 1,200" occurs inside the
        # legitimate "AED 1,200,000" and a substring check fails on it.
        assert f"- [ ] AED {int(value):,} ->" not in arabic, value


def test_it_asks_for_every_lexicon_term(arabic):
    import yaml

    entries = yaml.safe_load(
        (AGENT_DIR.parent / "data" / "lexicon.yaml").read_text(encoding="utf-8")
    )
    for entry in entries:
        assert str(entry["term"]) in arabic


def test_it_asks_for_every_prohibited_category_in_plain_words(arabic):
    """A reviewer cannot read a regular expression, and an uncovered category
    would simply be missing rather than obviously blank."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from ambassador.guardrails.prohibited import load_patterns

    for category in {p.category for p in load_patterns()}:
        assert category.replace("_", " ") in arabic
    # Described, not quoted: no raw pattern should reach the reviewer.
    assert "\\b" not in arabic


def test_the_arabic_packet_asks_for_dialect_and_the_hindi_one_does_not():
    """The two languages fail differently, and a packet that said "avoid MSA"
    to a Hindi speaker would read as boilerplate nobody checked."""
    assert "Modern Standard Arabic" in generate("ar")
    assert "Modern Standard Arabic" not in generate("hi")


def test_the_hindi_packet_carries_the_lakh_crore_warning_and_arabic_does_not():
    assert "24 lakh, never 2.4 crore" in generate("hi")
    assert "lakh" not in generate("ar")


def test_an_unknown_language_is_refused(tmp_path):
    result = subprocess.run(
        [sys.executable, str(TOOL), "fr"],
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        check=False,
    )
    assert result.returncode != 0
