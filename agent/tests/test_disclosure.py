"""The opening disclosure, and the gate that stops a call opening without one.

The defect this covers is not a wrong disclosure - it is no disclosure. The
copy existed in `data/disclosures.yaml` from the start, nothing anywhere
loaded the file, no call site spoke it, and `prompts.py` told the model the
system would handle it. Every branch of that was individually reasonable and
the result was a voice agent that never disclosed it was one, in any language.

No framework import here, so this runs in core-only mode with the rest.
"""

from __future__ import annotations

from typing import get_args

import pytest

from adapter.disclosure import (
    UncertifiedLanguageError,
    load_disclosures,
    resolve_opening,
)
from ambassador.schemas import Language

LANGUAGES = get_args(Language)


def write(tmp_path, body: str):
    path = tmp_path / "disclosures.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- the shipped file -----------------------------------------------------


def test_english_is_certified_and_every_uncertified_language_is_gated():
    """An invariant, not a snapshot of today's file.

    Naming ar/hi here would turn a native reviewer's first delivery into a
    failing test. This says the same thing without dating: whatever is
    uncertified, a call may not open in it.
    """
    disclosures = load_disclosures()
    assert disclosures.is_certified("en"), "the fallback language must have copy"

    gated = [lang for lang in LANGUAGES if not disclosures.is_certified(lang)]
    for language in gated:
        with pytest.raises(UncertifiedLanguageError, match=repr(language)):
            resolve_opening(disclosures, language, allow_uncertified=False)


def test_the_shipped_english_copy_says_transcribed_not_recorded():
    """docs/04-: the notice must match what is actually retained.

    The POC stores no raw audio. "Recorded" would be a false statement to a
    buyer about their own data, which is worse than an awkward sentence.
    """
    english = load_disclosures().copy["en"].lower()
    assert "transcribed" in english
    assert "recorded" not in english


# --- the gate -------------------------------------------------------------


def test_a_certified_language_opens_in_itself(tmp_path):
    path = write(tmp_path, 'en: "English notice."\nar: "إشعار عربي."\nhi: ""\n')
    disclosures = load_disclosures(path)

    copy, spoken_in = resolve_opening(disclosures, "ar", allow_uncertified=False)
    assert spoken_in == "ar"
    assert copy == "إشعار عربي."


def test_the_override_opens_in_english_and_says_which_language_it_is_in(tmp_path):
    """The degraded path docs/04- asks for, with the degradation legible.

    The second return value is the whole point: the caller has to be able to
    report that an Arabic call opened in English, or the event stream would
    show a certified Arabic opening that never happened.
    """
    path = write(tmp_path, 'en: "English notice."\nar: ""\nhi: ""\n')
    disclosures = load_disclosures(path)

    copy, spoken_in = resolve_opening(disclosures, "ar", allow_uncertified=True)
    assert copy == "English notice."
    assert spoken_in == "en"  # not "ar", which is what makes it reportable


def test_the_refusal_names_the_file_the_language_and_the_way_out(tmp_path):
    """A start-up error an operator can act on without reading this module."""
    path = write(tmp_path, 'en: "English notice."\nar: ""\nhi: ""\n')
    with pytest.raises(UncertifiedLanguageError) as caught:
        resolve_opening(load_disclosures(path), "ar", allow_uncertified=False)

    message = str(caught.value)
    assert "disclosures.yaml" in message
    assert "'ar'" in message
    assert "ALLOW_UNCERTIFIED_LANGUAGE" in message


# --- the loader -----------------------------------------------------------


def test_a_language_missing_from_the_file_is_uncertified_not_a_crash(tmp_path):
    """Absence and emptiness are the same state, and neither is an exception.

    A KeyError here would land at start-up rather than mid-call, so it is not
    dangerous - but it is the wrong shape. Absence is a normal, expected
    condition for a language nobody has reviewed yet.
    """
    disclosures = load_disclosures(write(tmp_path, 'en: "English notice."\n'))
    assert disclosures.certified == frozenset({"en"})


def test_whitespace_only_copy_does_not_count_as_a_disclosure(tmp_path):
    path = write(tmp_path, 'en: "English notice."\nar: "   \\n  "\n')
    assert not load_disclosures(path).is_certified("ar")


def test_no_english_copy_fails_at_load_because_nothing_could_fall_back(tmp_path):
    with pytest.raises(ValueError, match="nothing to say"):
        load_disclosures(write(tmp_path, 'ar: "إشعار عربي."\n'))


def test_a_non_string_disclosure_is_refused_rather_than_spoken(tmp_path):
    """YAML reads bare yes/no/on/off as booleans and bare digits as numbers.

    The fallback loader documents the same trap. Coercing with str() would
    synthesise the word "True" at a buyer.
    """
    with pytest.raises(ValueError, match="not text"):
        load_disclosures(write(tmp_path, 'en: "English notice."\nar: no\n'))


def test_a_file_of_the_wrong_shape_gives_a_message_not_an_attribute_error(tmp_path):
    with pytest.raises(ValueError, match="must be a mapping"):
        load_disclosures(write(tmp_path, "- just\n- a list\n"))
