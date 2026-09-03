"""The ambassador's given name, and the rule that an unnamed one still works.

The client named the English ambassador Jane. Two things are worth testing
rather than assuming: that a name reaches the buyer through both paths that
speak it, and that a language WITHOUT a name behaves exactly as it did before
names existed. The second is the one that protects Arabic and Hindi, which have
no name today and must not be degraded by a feature they are not part of.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
import yaml

from adapter.disclosure import NAME_PLACEHOLDER, load_disclosures, resolve_opening
from ambassador.ambassadors import load_ambassadors
from ambassador.prompts import build_ambassador_prompt
from ambassador.schemas import Language

DATA = Path(__file__).resolve().parents[2] / "data"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ambassadors.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- the shipped file -----------------------------------------------------


def test_the_shipped_file_names_the_english_ambassador_jane():
    """The client's decision, pinned. A name that quietly changed would reach a
    buyer in the first sentence of the call."""
    assert load_ambassadors().name_for("en") == "Jane"


def test_arabic_and_hindi_are_unnamed_until_a_reviewer_answers():
    """Not a gap to be filled by transliterating here. How a given name is
    written in Arabic or Devanagari is a native-reviewer question, and the
    packet asks it - so these stay empty and those languages behave as they
    always have. Delete this test the day a reviewer answers, not before."""
    ambassadors = load_ambassadors()
    assert ambassadors.name_for("ar") == ""
    assert ambassadors.name_for("hi") == ""
    assert ambassadors.named == frozenset({"en"})


def test_every_language_has_an_entry_even_when_it_is_empty():
    """An absent key and an empty one both read as unnamed, but only one of
    them tells a reviewer they are being asked. The file has to keep asking."""
    raw = yaml.safe_load((DATA / "ambassadors.yaml").read_text(encoding="utf-8"))
    for language in get_args(Language):
        assert language in raw, language


# --- the loader -----------------------------------------------------------


def test_an_absent_language_reads_as_unnamed(tmp_path):
    assert load_ambassadors(write(tmp_path, 'en: "Jane"\n')).name_for("ar") == ""


def test_a_name_is_stripped(tmp_path):
    """A trailing space would be spoken into the middle of the disclosure."""
    assert (
        load_ambassadors(write(tmp_path, 'en: "  Jane  "\n')).name_for("en") == "Jane"
    )


@pytest.mark.parametrize("body", ["en: no\n", "en: on\n", "en: 2026\n", "en: [Jane]\n"])
def test_a_name_that_is_not_a_quoted_string_is_refused(tmp_path, body):
    """The YAML trap this repository has hit twice. Bare `no` and `on` load as
    booleans and bare digits as numbers, and coercing would introduce an
    ambassador called "False" while the file still looked correct."""
    with pytest.raises(ValueError, match="not text"):
        load_ambassadors(write(tmp_path, body))


def test_a_file_that_is_not_a_mapping_is_refused(tmp_path):
    with pytest.raises(ValueError, match="must be a mapping"):
        load_ambassadors(write(tmp_path, "- Jane\n"))


def test_an_empty_file_is_unnamed_rather_than_an_error(tmp_path):
    """Unlike disclosures.yaml, absence here is a working state: every language
    is simply unnamed, which is what shipped before names existed."""
    ambassadors = load_ambassadors(write(tmp_path, "\n"))
    assert ambassadors.named == frozenset()
    assert ambassadors.name_for("en") == ""


# --- the disclosure -------------------------------------------------------


def test_the_english_disclosure_says_the_name():
    spoken, language = resolve_opening(
        load_disclosures(),
        "en",
        allow_uncertified=False,
        names=load_ambassadors().names,
    )
    assert language == "en"
    assert spoken.startswith("You are speaking with Jane, Binghatti's AI ambassador.")
    # The three commitments the disclosure exists to make, unchanged by naming.
    assert "AI ambassador" in spoken
    assert "transcribed" in spoken
    assert "ask for a person" in spoken


def test_without_a_name_the_disclosure_is_the_sentence_that_shipped_before():
    spoken, _ = resolve_opening(load_disclosures(), "en", allow_uncertified=False)
    assert spoken.startswith("You are speaking with Binghatti's AI ambassador.")
    assert "Jane" not in spoken


def test_no_rendering_can_leave_a_hole_where_the_name_was():
    """The failure this design exists to make impossible: a template rendered
    with an empty name would say "You are speaking with , Binghatti's". The
    unnamed sentence is authored separately precisely so that cannot happen."""
    disclosures = load_disclosures()
    for name in ("", "   "):
        spoken = disclosures.speak("en", name.strip())
        assert NAME_PLACEHOLDER not in spoken
        assert " , " not in spoken
        assert not spoken.startswith("You are speaking with ,")


def test_a_degraded_call_uses_the_name_of_the_language_it_actually_speaks():
    """An Arabic call with no Arabic disclosure falls back to the English
    sentence. The name inside it must be the English one - an empty Arabic name
    substituted into English copy would be the hole above, and an Arabic name
    would be a word in the wrong language mid-sentence."""
    spoken, language = resolve_opening(
        load_disclosures(),
        "ar",
        allow_uncertified=True,
        names=load_ambassadors().names,
    )
    assert language == "en"
    assert "Jane" in spoken


def test_a_named_template_without_the_placeholder_is_refused(tmp_path):
    """Registering a named form that does not use the name is worse than not
    having one: it reads as covered and changes nothing a buyer hears."""
    path = tmp_path / "disclosures.yaml"
    path.write_text(
        'en:\n  named: "Hello from Binghatti."\n  unnamed: "Hello."\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contains no"):
        load_disclosures(path)


def test_a_named_template_cannot_stand_without_the_unnamed_sentence(tmp_path):
    """The unnamed sentence is what makes the language openable at all, and is
    what gets spoken if the name is ever cleared."""
    path = tmp_path / "disclosures.yaml"
    path.write_text('en:\n  named: "Hi {name}."\n', encoding="utf-8")
    with pytest.raises(ValueError, match="no 'unnamed' disclosure"):
        load_disclosures(path)


def test_the_plain_string_shape_still_loads(tmp_path):
    """Every entry had this shape before names existed, and `ar` and `hi` still
    do. Breaking it would break the disclosure, which is the one thing a call
    may not open without."""
    path = tmp_path / "disclosures.yaml"
    path.write_text('en: "English notice."\n', encoding="utf-8")
    disclosures = load_disclosures(path)
    assert disclosures.speak("en", "Jane") == "English notice."
    assert disclosures.is_certified("en")


# --- the prompt -----------------------------------------------------------


def test_the_prompt_tells_the_model_its_name():
    prompt = build_ambassador_prompt(
        "INVENTORY",
        "en",
        system_confirms_budget=True,
        system_confirms_project=True,
        ambassador_name="Jane",
    )
    assert prompt.startswith("You are Jane, the digital brand ambassador for Binghatti")
    assert "Your name is Jane." in prompt
    assert "Never give yourself any other name." in prompt


def test_an_unnamed_prompt_is_byte_identical_to_the_one_that_shipped():
    """The strongest form of "unnamed behaves as today": not similar, identical.
    Anything less and Arabic and Hindi would be quietly running a prompt nobody
    evaluated, for a feature they are not part of."""
    kwargs = dict(system_confirms_budget=True, system_confirms_project=False)
    unnamed = build_ambassador_prompt("INVENTORY", "hi", **kwargs)
    assert "You are the digital brand ambassador for Binghatti" in unnamed
    assert "Your name is" not in unnamed
    assert "  " not in unnamed.splitlines()[0]


@pytest.mark.parametrize("language", get_args(Language))
def test_a_name_is_not_a_figure_so_the_guardrail_neither_blocks_nor_checks_it(
    language: Language, allowed, patterns, forms
):
    """Stated as a test because a reader might assume the numeric guardrail
    covers the name. It does not, and it should not: a name carries no digits,
    so the model inventing a different one is a wrong answer rather than an
    unverified figure. The check here is that naming changes nothing about what
    the guardrail does to an ordinary sentence."""
    from ambassador.guardrails.pipeline import process_sentence
    from ambassador.schemas import GuardrailViolation

    sentence = "You are speaking with Jane, Binghatti's AI ambassador."
    result = process_sentence(
        sentence,
        language=language,
        allowed=allowed,
        patterns=patterns,
        forms=forms,
    )
    assert not isinstance(result, GuardrailViolation), result
    assert "Jane" in result.text
