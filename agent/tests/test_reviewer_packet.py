"""The native-reviewer packet generator.

Three issues (#4, #14, #15) wait on one person's time, so the packet has to be
right the first time - a second session to collect what the first one missed
costs another calendar week.

The property that matters is COMPLETENESS: the packet is generated from the
same loaders the runtime uses, so it asks for exactly what the system will
demand back. These tests are what notices when it stops doing that.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
TOOL = AGENT_DIR / "tools" / "reviewer_packet.py"

# Derived from the tool, never restated: the packet's language set is the
# tool's own, and a copy here would be a second source of truth for it.
sys.path.insert(0, str(AGENT_DIR / "tools"))
from reviewer_packet import LANGUAGE_NAMES  # noqa: E402

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


def test_the_ambassadors_name_is_asked_for_in_4b_not_the_name_list():
    """The name is a lexicon term, but section 4 is the wrong place to ask.

    It is exempt from section 4's list on purpose: that list's preamble is
    about the client's name and Dubai places, and section 4 would be asking
    how to SAY a name whose written form is still open one section below.

    Without this test that exemption would be a silent loss of coverage,
    because `test_it_asks_for_every_lexicon_term` is satisfied by the name
    merely appearing in 4b's prose - it would pass while nothing asked for the
    respelling at all.
    """
    from ambassador.ambassadors import load_ambassadors

    name = load_ambassadors().name_for("en")
    assert name, "no English ambassador name to check"

    packet = generate("ar")
    section_4 = packet.split("## 4. How these names should sound")[1]
    name_list, section_4b = section_4.split("## 4b.")

    assert f"- [ ] {name} ->" not in name_list, (
        f"{name} is in section 4's list, where it reads as a place and "
        "presupposes 4b's answer"
    )
    assert "said aloud" in section_4b, (
        f"nothing asks how {name} should be said; the respelling has no ask"
    )


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


# --- the two things the packet was not asking for --------------------------
#
# Both slipped for one structural reason: the packet is generated from the
# loaders the runtime uses, so a `VERIFY:` in a file no loader reads cannot
# appear in it. The hotline had a whitelist entry and no row to enumerate;
# `prerolls.yaml` had no loader at all. Fixed at the wiring, so these tests
# check the wiring rather than the words.


def test_it_asks_how_the_hotline_is_read_aloud(arabic):
    """The escalation path, which AGENTS.md says gets the same polish as the
    happy path. Today the digits go to the voice and are read as "eighty
    thousand and fifteen", and the packet said nothing about it."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from ambassador.inventory import build_allowed_figures, load_inventory

    allowed = build_allowed_figures(load_inventory())
    assert allowed.identifiers, "no identifiers - this test would pass vacuously"
    for value in allowed.identifiers:
        assert f"- [ ] {int(value)} (" in arabic, value


def test_it_asks_for_the_hotline_away_from_the_money(arabic):
    """The trap the separation exists for. Section 3 tells the reviewer to name
    the currency inside every phrase, and a hotline number that inherited that
    instruction becomes a sum of dirhams. So it is asked for in its own
    section, and never as an `AED` line item."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from ambassador.inventory import build_allowed_figures, load_inventory

    allowed = build_allowed_figures(load_inventory())
    for value in allowed.identifiers:
        assert f"- [ ] AED {int(value):,} ->" not in arabic, value
    section = arabic.split("## 3e")[1].split("## 4")[0]
    assert "naming no currency" in section


def test_the_hotline_ask_is_labelled_from_the_whitelist_not_typed(arabic):
    """Generated, like everything else here. A hand-written "Binghatti's
    toll-free hotline" beside the number would read correctly today and be
    wrong the day a permit number is whitelisted, with nothing to notice."""
    import yaml

    data = yaml.safe_load(
        (AGENT_DIR.parent / "data" / "whitelist.yaml").read_text(encoding="utf-8")
    )
    identifiers = [e for e in data["amounts"] if e["kind"] == "identifier"]
    assert identifiers, "no identifier in the whitelist - vacuous"
    for entry in identifiers:
        assert str(entry["why"]).split(".")[0].strip() in arabic


def test_it_asks_for_the_prerolls(arabic):
    """`data/prerolls.yaml` carries `ar: []` and `hi: []` under a `VERIFY:`
    marker, and until it had a loader that marker could not reach this page."""
    sys.path.insert(0, str(AGENT_DIR / "src"))
    from adapter.prerolls import load_prerolls

    section = arabic.split("## 2b")[1].split("## 3.")[0]
    for line in load_prerolls().for_language("en"):
        assert line in section, line
    assert section.count("- [ ] Arabic acknowledgment:") == 2


def test_the_preroll_ask_says_we_will_not_borrow_the_english(arabic):
    """The reviewer has to know that "no natural equivalent" is an acceptable
    answer, or they will invent one. An English filler in an Arabic call is a
    seam the buyer hears rather than one it hides."""
    section = arabic.split("## 2b")[1].split("## 3.")[0]
    assert "we play nothing" in section


# --- packet completeness against the data directory ------------------------
#
# The tests above each guard ONE copy type, and each was added beside the
# artifact it guards. That is why none of them caught #81: a new native-copy
# file arrives with no test, so nothing was watching the set itself.

DATA_DIR = AGENT_DIR.parent / "data"

# A file needs native authorship if it SAYS so. Matching the marker rather than
# listing the files is the whole point: a list here would be the same
# hand-maintained thing that already failed once. The narrower phrasings are
# deliberate - `whitelist.yaml` carries `VERIFY:` markers too, but they ask for
# a Golden Visa threshold and a phone number from the client, which is factual
# verification and not a language anyone has to speak.
_NATIVE_MARKER = re.compile(
    r"native[-\s](?:authored|speaker|reviewer)|written by a native",
    re.IGNORECASE,
)


def packet_module():
    """The tool as a module, for reading its declarations rather than its output."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("reviewer_packet", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_copy_files() -> set[str]:
    """Every data file that asks for native-authored copy, found by reading."""
    return {
        path.name
        for path in DATA_DIR.glob("*.yaml")
        if _NATIVE_MARKER.search(path.read_text(encoding="utf-8"))
    }


def test_every_native_copy_file_has_a_section_in_the_packet():
    """The guard #81 did not have.

    `farewells.yaml` landed needing an authored farewell, a closing-phrase
    list and a courtesy list in both languages, and the packet did not ask for
    any of it. Every individual test above still passed, because each watches
    its own artifact and none watches the set.

    So this compares the packet's declared sections against the data directory
    itself. The day another native-copy file lands without a section, this
    fails, and it fails in the only window that matters: before the native
    session it would have been collected in.
    """
    missing = native_copy_files() - set(packet_module().NATIVE_COPY_SECTIONS)
    assert not missing, (
        f"these data files ask for native-authored copy and the packet has no "
        f"section for them: {sorted(missing)}. Add a section to "
        f"tools/reviewer_packet.py and register it in NATIVE_COPY_SECTIONS, "
        f"rather than registering it without asking."
    )


def test_the_registry_does_not_claim_files_that_no_longer_need_native_copy():
    """The other direction, so the table cannot rot into a fiction.

    A stale entry is not harmless: it is a heading this test would then demand
    forever, which is how a packet ends up asking a reviewer for copy nothing
    reads.
    """
    stale = set(packet_module().NATIVE_COPY_SECTIONS) - native_copy_files()
    assert not stale, (
        f"NATIVE_COPY_SECTIONS names files that no longer ask for native "
        f"copy: {sorted(stale)}"
    )


@pytest.mark.parametrize("language", sorted(LANGUAGE_NAMES))
def test_every_registered_section_is_actually_in_the_generated_packet(language):
    """Registering a section is not the same as writing one.

    Without this, `NATIVE_COPY_SECTIONS` could be brought up to date by adding
    a line to a dict, which would satisfy the test above while the reviewer's
    document still asks for nothing.
    """
    packet = generate(language)
    sections = packet_module().NATIVE_COPY_SECTIONS
    for filename, heading in sorted(sections.items()):
        assert heading in packet, (
            f"{filename} is registered to section {heading!r}, which is not in "
            f"the generated {language} packet"
        )
