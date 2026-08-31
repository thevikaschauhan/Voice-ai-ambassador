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


# --- section 5b: the agent's own words ------------------------------------
#
# The rest of the packet reviews copy WE wrote. This section is the only place a
# native speaker sees what the MODEL says, which is what a buyer actually hears
# and what nobody on the build team can read.


def test_it_shows_the_recorded_replies_for_that_language(arabic):
    """Every recorded ar fixture has to reach the packet, or the reviewer
    blesses a subset and the rest ships unread."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from evals.cases import load_cases

    expected = []
    for case in load_cases():
        if case.language != "ar":
            continue
        for turn in case.turns:
            fixture = turn.model
            while fixture is not None:
                if fixture.source == "recorded" and fixture.text.strip():
                    expected.append(" ".join(fixture.text.split()))
                fixture = fixture.retry
    assert expected, "no recorded Arabic fixtures - this test would pass vacuously"
    for reply in expected:
        assert reply in arabic


def test_it_does_not_ask_a_reviewer_to_bless_our_invented_arabic(arabic):
    """An authored fixture is text the build team made up to stand for a model
    behaviour. A native speaker's time is the scarcest thing in the project and
    must not go on copy no buyer will ever hear."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from evals.cases import load_cases

    authored = [
        " ".join(f.text.split())
        for case in load_cases()
        if case.language == "ar"
        for turn in case.turns
        for f in _fixtures(turn.model)
        if f.source == "authored" and f.text.strip()
    ]
    assert authored, "no authored Arabic fixtures - this test would pass vacuously"
    section = arabic.split("## 5b")[1].split("## 6")[0]
    for text in authored:
        assert text not in section


def _fixtures(fixture):
    while fixture is not None:
        yield fixture
        fixture = fixture.retry


def test_the_hindi_packet_asks_about_the_transliterated_currency():
    """Recorded live: the model wrote AED as a Devanagari transliteration. It is
    the product's own choice, not ours, so the only way it gets checked is by
    asking - and a reviewer handed "does this read naturally?" and nothing else
    will say yes."""
    packet = generate("hi")
    section = packet.split("## 5b")[1].split("## 6")[0]
    assert "transliteration" in section
    assert "एडीई" in section


def test_each_language_is_asked_about_its_own_observations():
    """The Arabic reply keeps Latin project names; the Hindi one transliterates
    the currency. Asking each language the other's question wastes the session."""
    assert "Latin script" in generate("ar").split("## 5b")[1]
    assert "Latin script" not in generate("hi").split("## 5b")[1].split("## 6")[0]


def test_a_language_with_nothing_recorded_says_so_rather_than_going_blank(
    monkeypatch, capsys
):
    """An empty section reads as "nothing to review here". The truth would be
    that nobody has recorded that language yet, which is itself the finding, and
    a packet that hides it books a reviewer session against copy the product may
    not even produce."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("reviewer_packet", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "load_cases", lambda: [])
    module.main("ar")
    section = capsys.readouterr().out.split("## 5b")[1].split("## 6")[0]
    assert "Nothing recorded yet" in section
    assert "uv run eval --live" in section
