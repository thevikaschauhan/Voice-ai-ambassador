"""Language changes use final STT evidence and retain conversation state."""

from typing import get_args

import pytest

from ambassador.schemas import Language
from ambassador.language_switch import choose_language


@pytest.mark.parametrize("language", get_args(Language))
def test_requested_languages_are_supported(language):
    # The `language in get_args(Language)` assertion that used to be here was
    # a tautology - the parameter comes from get_args(Language), so it could
    # not fail, and it added ten cases to the count without asking anything.
    assert choose_language("en", [(language, 20)]) == language


def test_ambiguous_or_short_evidence_keeps_the_current_language():
    assert choose_language("fr", [("en", 3)]) == "fr"
    assert choose_language("en", [("fr", 10), ("en", 10)]) == "en"
    assert choose_language("en", [("unknown", 50)]) == "en"
    assert choose_language("en", []) == "en"


def test_regional_codes_are_normalised_and_chinese_needs_no_spaces():
    assert choose_language("en", [("pt-BR", 20)]) == "pt"
    assert choose_language("en", [("cmn", 4)]) == "zh"
    assert choose_language("en", [("zh-CN", 4)]) == "zh"


def test_unsupported_speech_does_not_make_a_small_supported_fragment_win():
    assert choose_language("fr", [("it", 100), ("en", 12)]) == "fr"
