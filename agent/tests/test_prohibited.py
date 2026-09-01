import re
from typing import get_args

import pytest
import yaml

from ambassador.guardrails.prohibited import (
    ProhibitedPattern,
    check_prohibited,
    languages_covered,
    load_patterns,
    patterns_for,
)
from ambassador.inventory import DATA_DIR
from ambassador.schemas import Language


def test_guarantee_language_is_caught(patterns):
    for text in [
        "This offers a guaranteed 8% rental yield.",
        "Returns are guaranteed by the developer.",
        "It is a risk-free investment.",
        "You can't lose with Business Bay.",
    ]:
        assert check_prohibited(text, patterns, "en"), text


def test_advice_and_certainty_are_caught(patterns):
    for text in [
        "You should buy this now.",
        "I recommend you invest in Skyrise.",
        "Prices will rise after handover.",
        "This area is certain to appreciate.",
    ]:
        assert check_prohibited(text, patterns, "en"), text


def test_regulatory_overreach_is_caught(patterns):
    assert check_prohibited(
        "Your visa approval is guaranteed at this price.", patterns, "en"
    )
    assert check_prohibited("You will get the golden visa.", patterns, "en")


def test_composed_factual_language_passes(patterns):
    for text in [
        "The payment plan asks for 20% at booking.",
        "Handover is planned for Q4 2026.",
        "Many buyers appreciate the Business Bay location.",
        "I can connect you with an ambassador to discuss terms.",
    ]:
        assert check_prohibited(text, patterns, "en") == [], text


# --- the routing rule: English always, plus the sentence's own language ------
#
# `language` was loaded and never read, which read as if the validator were
# language-aware and merely under-populated - it was neither, and that was the
# finding in issue #14. It now decides something, under one deliberately
# asymmetric rule, and both halves of the asymmetry get a test: the English
# half because losing it loses the only ar/hi coverage that exists, and the
# per-language half because it is what makes an authored Arabic pattern not
# fire on a Hindi call.


def test_an_english_violation_is_caught_inside_an_arabic_sentence(patterns):
    """The half of the rule that must never be traded away.

    Arabic-English code-switching is the default Dubai register, not an edge
    case. Routing purely by the call's language would silently stop catching
    this, and it is the one form of ar/hi violation currently catchable at all.
    Note the call language is "ar": that is what would break under a naive
    filter.
    """
    hits = check_prohibited(
        "هذا المشروع يقدم guaranteed returns للمستثمرين.", patterns, "ar"
    )
    assert hits, "an English guarantee inside Arabic text must still be caught"
    assert "return_guarantees" in hits[0]


def test_an_english_violation_is_caught_inside_a_hindi_sentence(patterns):
    hits = check_prohibited("यह प्रोजेक्ट risk-free निवेश है।", patterns, "hi")
    assert hits
    assert "return_guarantees" in hits[0]


def test_english_patterns_apply_to_every_language_the_product_offers(patterns):
    """The rule stated directly, so it cannot rot into "en only" by accident.

    Derived from the Literal rather than a hand-typed tuple, so a fourth
    language added to the product is covered by this test the day it lands.
    """
    english = [p for p in patterns if p.language == "en"]
    assert english, "the shipped file is English-only; this test assumes that"
    for language in get_args(Language):
        assert set(english) <= set(patterns_for(patterns, language)), language


# A TEST-ONLY pattern. It is not Arabic copy and makes no claim to be: it is a
# Latin-script marker word carrying `language: ar`, which is all that is needed
# to prove the routing. Authoring real Arabic patterns is native-reviewer work
# and AGENTS.md forbids doing it here (see data/prohibited-patterns.yaml).
AR_ONLY_FIXTURE = ProhibitedPattern(
    category="return_guarantees",
    language="ar",
    regex=re.compile(r"\bZZTESTONLYAR\b", re.IGNORECASE),
)


def test_a_pattern_authored_for_one_language_applies_only_to_that_language(patterns):
    """The per-language half. Without it, the Arabic patterns a reviewer writes
    would fire on Hindi calls, where a false positive is a sentence the buyer
    never hears."""
    with_fixture = [*patterns, AR_ONLY_FIXTURE]
    sentence = "the marker ZZTESTONLYAR appears here"

    assert check_prohibited(sentence, with_fixture, "ar")
    assert check_prohibited(sentence, with_fixture, "hi") == []
    assert check_prohibited(sentence, with_fixture, "en") == []

    assert AR_ONLY_FIXTURE in patterns_for(with_fixture, "ar")
    assert AR_ONLY_FIXTURE not in patterns_for(with_fixture, "hi")
    assert AR_ONLY_FIXTURE not in patterns_for(with_fixture, "en")


def test_the_language_argument_is_required(patterns):
    """No default, on purpose. A default makes the routing rule skippable by
    omission on the compliance validator, which is the shape of the original
    finding."""
    with pytest.raises(TypeError):
        check_prohibited("You should buy this now.", patterns)


def test_factual_non_english_text_is_not_blocked_by_english_patterns(patterns):
    """The cost of applying English everywhere, checked rather than assumed.

    Over-blocking is the safe direction, but it is not free: a false positive
    is a sentence the buyer never hears. Scripts differ, so this should be
    clean, and this test is what would notice if a future pattern were written
    loosely enough to match across a script boundary.
    """
    for text, language in [
        ("خطة السداد تطلب ٢٠٪ عند الحجز.", "ar"),
        ("हैंडओवर Q4 2026 के लिए योजनाबद्ध है।", "hi"),
        ("يمكنني توصيلك بأحد مستشارينا.", "ar"),
    ]:
        assert check_prohibited(text, patterns, language) == [], text


def test_coverage_reports_the_languages_actually_authored(patterns):
    """A set claim, not a count, and not a snapshot of today's file.

    Naming "English only" here would fail the day a native reviewer delivers
    Arabic patterns. This says the reported set is exactly what the file
    carries, which stays true either way.
    """
    assert languages_covered(patterns) == {p.language for p in patterns}


def test_coverage_is_honest_about_a_language_with_no_patterns(tmp_path):
    """The gap this exists to surface: a language the system offers and the
    guardrail does not cover."""
    source = write_complete(
        tmp_path, [("return_guarantees", "en", [r"\brisk[-\s]?free\b"])]
    )
    covered = languages_covered(load_patterns(source))
    assert "en" in covered
    assert "ar" not in covered


def test_a_declared_but_empty_slot_does_not_read_as_coverage(patterns):
    """The shipped file DECLARES ar and hi with empty pattern lists, so the
    reviewer writes into a validated structure. Declaring must not be mistaken
    for covering - that would restate the exact impression issue #14 was about.
    """
    assert languages_covered(patterns) == frozenset({"en"})
    assert patterns_for(patterns, "ar") == patterns_for(patterns, "en")


def test_the_shipped_file_declares_every_category_for_ar_and_hi():
    """The reviewer's slots are real, complete, and EMPTY.

    The loader enforces the first two now (it validates the matrix), so what
    this adds is the third: the ar and hi slots must carry no patterns, because
    nobody here may author them. Delete this test the day a native reviewer
    delivers, not before.
    """
    raw = yaml.safe_load(
        (DATA_DIR / "prohibited-patterns.yaml").read_text(encoding="utf-8")
    )
    declared = {(g["language"], g["category"]) for g in raw}
    english = {c for lang, c in declared if lang == "en"}
    assert english
    for language in (lang for lang in get_args(Language) if lang != "en"):
        missing = english - {c for lang, c in declared if lang == language}
        assert not missing, f"{language} has no slot for {sorted(missing)}"
        for category in english:
            (group,) = [
                g
                for g in raw
                if g["language"] == language and g["category"] == category
            ]
            assert group["patterns"] == [], (
                f"{language}/{category} carries patterns nobody here may have "
                "authored - VERIFY: native review (AGENTS.md)"
            )


# --- the loader reports its own failures -----------------------------------
#
# All of these already failed at start-up rather than mid-call, so none is a
# correctness fix. The next person to edit this file is an engineer
# transcribing a native reviewer's patterns, and a bare KeyError with no
# filename is a poor thing to hand them.


def write_patterns(tmp_path, body: str):
    """Raw, exactly as given. For fixtures that are deliberately malformed."""
    path = tmp_path / "prohibited-patterns.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def write_complete(tmp_path, groups: list[tuple[str, str, list[str]]]):
    """Write a file whose (category, language) matrix is COMPLETE.

    The loader requires one slot per language for every category that appears,
    so a fixture about anything else has to satisfy that first. Pass only the
    groups the test cares about; the rest are filled with `patterns: []`, which
    is what the shipped file does.
    """
    declared = {(category, language) for category, language, _ in groups}
    body = [
        {"category": category, "language": language, "patterns": patterns}
        for category, language, patterns in groups
    ]
    for category in sorted({name for name, _ in declared}):
        for language in sorted(get_args(Language)):
            if (category, language) not in declared:
                body.append(
                    {"category": category, "language": language, "patterns": []}
                )
    path = tmp_path / "prohibited-patterns.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


def test_a_group_missing_its_language_says_so_and_says_why_it_matters(tmp_path):
    path = write_patterns(tmp_path, '- category: c\n  patterns:\n    - "x"\n')
    with pytest.raises(ValueError, match="has no 'language'"):
        load_patterns(path)


def test_a_group_missing_its_patterns_says_so(tmp_path):
    with pytest.raises(ValueError, match="has no 'patterns'"):
        load_patterns(write_patterns(tmp_path, "- category: c\n  language: ar\n"))


def test_an_explicitly_empty_pattern_list_is_a_declaration_not_an_error(tmp_path):
    """An absent key is an accident; `patterns: []` is a slot waiting for its
    author. The distinction is what lets the ar and hi gap live in the data file
    instead of in a comment, which this repo has learned reads as configuration
    and is not."""
    loaded = load_patterns(write_complete(tmp_path, [("c", "ar", [])]))
    assert loaded == []
    assert languages_covered(loaded) == frozenset()


def test_a_null_pattern_list_is_still_an_error(tmp_path):
    """`patterns:` with nothing after it is a half-finished edit, not the
    deliberate empty list."""
    with pytest.raises(ValueError, match="has no 'patterns'"):
        load_patterns(
            write_patterns(tmp_path, "- category: c\n  language: ar\n  patterns:\n")
        )


def test_a_group_relabelled_to_another_valid_language_is_rejected(tmp_path):
    """Meredith's HIGH on PR #43, at the layer she asked for.

    The unknown-code guard below cannot see this one: `ar` is a language the
    product offers, so nothing about the group looks wrong. Reproduced against
    the SHIPPED file rather than a toy, because that is the edit somebody
    actually makes - move the English return_guarantees group to `ar` and the
    file loaded cleanly, `ar` calls still caught "risk-free", and English calls
    silently stopped catching it. Apply-everything had caught it in every
    language, so routing is what turned this into a hole.
    """
    raw = yaml.safe_load(
        (DATA_DIR / "prohibited-patterns.yaml").read_text(encoding="utf-8")
    )
    for group in raw:
        if group["category"] == "return_guarantees" and group["language"] == "en":
            group["language"] = "ar"
    path = tmp_path / "prohibited-patterns.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="both declare"):
        load_patterns(path)


def test_the_english_pattern_that_relabel_would_have_silenced(patterns):
    """The other half of the same finding: what the group is FOR.

    Guarding the matrix is only worth it if the group it protects is doing
    work, so this pins the exact sentence the relabel let through on an
    English call.
    """
    assert check_prohibited("It is a risk-free investment.", patterns, "en")


def test_a_duplicate_category_and_language_pair_is_rejected(tmp_path):
    """Two slots for the same pair is the shape a mislabel leaves behind, and
    it is also just ambiguous: two places to edit, one of which is dead."""
    with pytest.raises(ValueError, match="both declare"):
        load_patterns(
            write_complete(
                tmp_path,
                [
                    ("c", "ar", ["x"]),
                    ("c", "ar", ["y"]),
                    ("c", "en", []),
                    ("c", "hi", []),
                ],
            )
        )


def test_a_category_missing_a_language_slot_is_rejected(tmp_path):
    """The other direction, and the shape of Meredith's one-group fixture: a
    category present for one language and absent for another is where a
    mislabel hides, and a category with no slot is one no reviewer is asked
    about."""
    for present in get_args(Language):
        groups = f'- category: c\n  language: {present}\n  patterns:\n    - "x"\n'
        with pytest.raises(ValueError, match="has no slot for"):
            load_patterns(write_patterns(tmp_path, groups))


def test_the_matrix_guard_runs_after_the_per_group_checks(tmp_path):
    """Ordering, asserted. A malformed group must still get its own specific
    message rather than a matrix complaint about the file it sits in - the
    person reading it is transcribing patterns, not auditing structure."""
    # A valid language, a broken regex, and an incomplete matrix all at once.
    # The regex message is the useful one, so it must win.
    path = write_patterns(
        tmp_path, '- category: c\n  language: ar\n  patterns:\n    - "[unclosed"\n'
    )
    with pytest.raises(ValueError, match="not a valid regular expression"):
        load_patterns(path)


def test_a_language_the_product_does_not_offer_is_rejected(tmp_path):
    """The failure mode routing introduces. Before filtering, `language: eng`
    was harmless because every pattern applied to every sentence. Now it
    switches the group off in silence: not English, so never always-applied,
    and matching no call language either. Fail-open on the compliance
    validator, so it fails at start-up instead."""
    for code in ("eng", "EN", "fr", "en-GB"):
        with pytest.raises(ValueError, match="not one of"):
            load_patterns(
                write_patterns(
                    tmp_path,
                    f'- category: c\n  language: {code}\n  patterns:\n    - "x"\n',
                )
            )


def test_an_invalid_regex_names_the_pattern_and_the_yaml_trap(tmp_path):
    """Doubling backslashes is the mistake this file invites, and the message
    should say so rather than leaving a bare `re.error`."""
    path = write_patterns(
        tmp_path, '- category: c\n  language: ar\n  patterns:\n    - "[unclosed"\n'
    )
    with pytest.raises(ValueError, match="not a valid regular expression"):
        load_patterns(path)


def test_an_empty_file_loads_as_no_patterns_rather_than_a_type_error(tmp_path):
    assert load_patterns(write_patterns(tmp_path, "")) == []


def test_a_file_of_the_wrong_shape_gives_a_message(tmp_path):
    with pytest.raises(ValueError, match="must be a list of pattern groups"):
        load_patterns(write_patterns(tmp_path, "category: c\n"))


# The last three loader branches, closed because AGENTS.md asks for 100% branch
# coverage on guardrail code and this is guardrail code. Each is a YAML shape a
# hand edit produces: a list entry that is not a mapping, a `patterns` value
# that is a bare string rather than a list, and a list entry that is not a
# string. None can reach a call - they all fail at start-up - which is the point.


def test_a_list_entry_that_is_not_a_mapping_says_so(tmp_path):
    with pytest.raises(ValueError, match="not a mapping"):
        load_patterns(write_patterns(tmp_path, "- just a string\n"))


def test_a_patterns_value_that_is_not_a_list_says_so(tmp_path):
    """The single-pattern shortcut somebody will try: `patterns: "\\brisk-free\\b"`
    without the dash. Iterating a string would compile each CHARACTER."""
    path = write_patterns(
        tmp_path, '- category: c\n  language: ar\n  patterns: "risk-free"\n'
    )
    with pytest.raises(ValueError, match="'patterns' must be a list"):
        load_patterns(path)


def test_a_pattern_that_is_not_a_string_says_so(tmp_path):
    """A bare `2026` in the list, or the YAML boolean trap: an unquoted `no`
    loads as False and could never match anything."""
    path = write_patterns(
        tmp_path, "- category: c\n  language: ar\n  patterns:\n    - 2026\n"
    )
    with pytest.raises(ValueError, match="must be a quoted regular"):
        load_patterns(path)
