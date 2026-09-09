"""One tokeniser, and the property that made replacing the old one safe.

`farewell.py` and `recognition.py` each carried `[^\\W_]+`. That pattern is
letters and digits only, so every combining mark - Devanagari matras and the
nukta, Arabic harakat - was a token boundary, and a word in those scripts
arrived as a run of consonant fragments. Two modules, identical bug, and a
farewell table that shattered its phrases exactly as it shattered the buyer's
utterance, so a round-trip test could not see it.

The tests here are mostly about NOT changing anything else. The old pattern is
kept as the baseline: outside marks and joiners the new tokeniser must agree
with it character for character, which is what makes this additive rather than
a rewrite of how every language is read.

Imports sit inside the tests on purpose (house convention): a module-level
import of a module that does not exist yet is one collection error, and this
file is meant to fail as a countable list of RED tests.
"""

from __future__ import annotations

import re

import pytest

# What the two modules used before. Not imported from anywhere: it is the
# behaviour being compared against, and inlining it is what lets this file
# still describe the change after the old pattern is gone.
OLD = re.compile(r"[^\W_]+", re.UNICODE)


def test_a_devanagari_word_is_one_token():
    from ambassador.tokens import tokens

    assert tokens("मुझे जाना है") == ["मुझे", "जाना", "है"]
    assert OLD.findall("मुझे जाना है") == ["म", "झ", "ज", "न", "ह"]


def test_the_nukta_does_not_end_a_word():
    """`figures.py` already knew this one: "करोड़" ends in a nukta, and the
    crore claim stayed an exempt count because `\\b` never held after it."""
    from ambassador.tokens import tokens

    assert tokens("करोड़") == ["करोड़"]


@pytest.mark.parametrize(
    "text",
    ["नमस्ते", "कीमत", "धन्यवाद"],
)
def test_devanagari_words_already_in_this_repo_are_single_tokens(text):
    """These three are the Devanagari strings the suite already carried, in
    tests that only ever asked whether they were content. They were fragmenting
    the whole time and nothing said so."""
    from ambassador.tokens import tokens

    assert tokens(text) == [text]
    assert len(OLD.findall(text)) > 1


def test_arabic_tokenises_the_same_with_and_without_harakat():
    """Whether the recogniser writes the vowels is not something an authored
    phrase list can know, so both spellings must reach the same tokens."""
    from ambassador.tokens import tokens

    assert tokens("مع السلامة") == ["مع", "السلامة"]
    assert tokens("مَعَ السَّلامَة") == ["مع", "السلامة"]


def test_a_joiner_does_not_split_a_word():
    """ZWNJ and ZWJ control how a conjunct renders and occur INSIDE one word.
    They are format characters, not letters, so the old pattern broke the word
    in half at them."""
    from ambassador.tokens import tokens

    assert tokens("क्‍ष") == ["क्‍ष"]
    assert tokens("अ‌ब") == ["अ‌ब"]


# --- everything that must not have changed -----------------------------------

UNCHANGED = [
    "goodbye",
    "that is all, thank you",
    "Thanks Jane, that is all for today, goodbye",
    "AED 985,000",
    "  ",
    "...",
    "no-more questions",
    "one_two",
    "مرحبا",
    "3 bedrooms",
]


@pytest.mark.parametrize("text", UNCHANGED)
def test_text_without_marks_tokenises_exactly_as_it_used_to(text):
    from ambassador.tokens import tokens

    assert tokens(text) == OLD.findall(text.lower())


def test_the_change_is_additive_across_the_whole_character_range():
    """The property behind this PR: for any character that is not a combining
    mark and not a joiner, the new tokeniser and the old pattern agree on
    whether it is part of a word. Sampled with a stride rather than every
    codepoint, so it stays a fast unit test while still crossing every block."""
    import unicodedata

    from ambassador.tokens import tokens

    disagreements = []
    for point in range(0, 0x110000, 61):
        char = chr(point)
        if unicodedata.category(char) in {"Mn", "Mc", "Me", "Cf"}:
            continue
        probe = f"a{char}b"
        if tokens(probe) != OLD.findall(probe.lower()):
            disagreements.append(hex(point))
    assert disagreements == []


def test_an_underscore_still_separates():
    """`[^\\W_]` excluded the underscore deliberately. A tokeniser that keeps
    it would make "one_two" a single word."""
    from ambassador.tokens import tokens

    assert tokens("one_two") == ["one", "two"]


def test_tokens_are_lowercased():
    """Every call site lowercased before matching. Doing it here means a new
    caller cannot forget and get a table that silently never matches."""
    from ambassador.tokens import tokens

    assert tokens("GOODBYE Jane") == ["goodbye", "jane"]


def test_a_mark_with_no_letter_in_front_of_it_is_not_a_word():
    """Malformed input, and the answer is that it carries no words rather than
    a token made of punctuation."""
    from ambassador.tokens import tokens

    assert tokens("े") == []
    assert tokens("ेे") == []


def test_content_is_a_letter_or_a_digit_in_any_script():
    from ambassador.tokens import has_content

    assert has_content("hello")
    assert has_content("مرحبا")
    assert has_content("नमस्ते")
    assert has_content("7")
    assert not has_content("  ")
    assert not has_content("...")


def test_a_bare_combining_mark_is_not_content():
    """`is_failed_recognition` calls an utterance with no content an empty
    turn. A stray matra the recogniser emitted around silence is not speech,
    so this must stay false - marks extend a word, they do not make one."""
    from ambassador.tokens import has_content

    assert not has_content("े")


def test_a_script_written_without_spaces_is_one_token_and_says_so():
    """The honest limit. Chinese and Japanese need a segmenter, not a
    character class, and pretending otherwise is how `retrieval.py`'s parity
    property came to hold over a single lexeme. One token per run is at least
    not FRAGMENTS, which is all this change claims."""
    from ambassador.tokens import tokens

    assert tokens("我要走了") == ["我要走了"]
