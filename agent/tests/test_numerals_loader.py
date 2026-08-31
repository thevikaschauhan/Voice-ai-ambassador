"""data/numerals.yaml is loaded and compiled, so it fails at start-up or not
at all.

The 2026-08-29 project learning is that a data file with no loader is a
document, not configuration. The corollary this file tests is the next
failure: a loader that accepts nonsense produces a guardrail that looks
configured and checks nothing. Every rejection below is a shape that would
otherwise degrade extraction SILENTLY, and each one names the file and the key
because the next person to edit it is transcribing a native reviewer's words.
"""

import pytest
import yaml

from ambassador.figures import (
    default_numerals,
    extract_figures,
    languages_covered,
    load_numerals,
)
from ambassador.inventory import DATA_DIR

SHIPPED = yaml.safe_load((DATA_DIR / "numerals.yaml").read_text(encoding="utf-8"))


def _write(tmp_path, raw):
    path = tmp_path / "numerals.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def _mutated(tmp_path, mutate):
    raw = yaml.safe_load((DATA_DIR / "numerals.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    return _write(tmp_path, raw)


def test_the_shipped_file_loads_and_english_is_the_only_covered_language():
    numerals = load_numerals()
    assert numerals.multipliers["crore"] == 10_000_000.0
    assert numerals.multipliers["lakh"] == 100_000.0
    assert languages_covered(numerals) == frozenset({"en"})


def test_the_default_is_read_once_and_is_the_shipped_file():
    assert default_numerals() is default_numerals()
    assert default_numerals().multipliers == load_numerals().multipliers


def test_the_file_must_be_a_mapping(tmp_path):
    path = tmp_path / "numerals.yaml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_numerals(path)


def test_symbols_must_be_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="'symbols' must be a mapping"):
        load_numerals(_mutated(tmp_path, lambda raw: raw.update(symbols=[])))


def test_a_symbol_list_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="symbols.percent must be a list"):
        load_numerals(
            _mutated(tmp_path, lambda raw: raw["symbols"].update(percent="%"))
        )


def test_a_word_list_must_hold_quoted_strings(tmp_path):
    # The YAML boolean trap, which has already shipped once in
    # data/currencies.yaml: a bare `no` loads as False and can never match.
    with pytest.raises(ValueError, match="quote every word"):
        load_numerals(
            _mutated(tmp_path, lambda raw: raw["symbols"].update(percent=[False]))
        )


def test_a_missing_list_is_allowed_and_reads_as_empty(tmp_path):
    # Only where something else still covers the job: dropping the native
    # currency words leaves the Latin tokens.
    numerals = load_numerals(
        _mutated(tmp_path, lambda raw: raw["languages"]["en"].pop("currency_words"))
    )
    assert extract_figures("AED 12", numerals)[0].figure.kind == "amount"


def test_a_group_separator_must_be_a_single_character(tmp_path):
    # It goes inside a character class, and stripping it must not change the
    # length of the text - verbalisation replaces the spans this module returns.
    with pytest.raises(ValueError, match="exactly one character"):
        load_numerals(
            _mutated(
                tmp_path, lambda raw: raw["symbols"].update(group_separators=["ab"])
            )
        )


def test_languages_must_be_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="'languages' must be a mapping"):
        load_numerals(_mutated(tmp_path, lambda raw: raw.update(languages=[])))


def test_a_language_block_must_be_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="languages.ar must be a mapping"):
        load_numerals(_mutated(tmp_path, lambda raw: raw["languages"].update(ar=[])))


def test_a_multiplier_table_must_be_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="languages.en.multipliers must be a mapping"):
        load_numerals(
            _mutated(
                tmp_path, lambda raw: raw["languages"]["en"].update(multipliers=["k"])
            )
        )


def test_a_multiplier_key_must_be_a_string(tmp_path):
    with pytest.raises(ValueError, match="non-string key"):
        load_numerals(
            _mutated(
                tmp_path,
                lambda raw: raw["languages"]["en"]["multipliers"].update({True: 1000}),
            )
        )


@pytest.mark.parametrize("factor", ["1000", True, None])
def test_a_multiplier_factor_must_be_a_number(tmp_path, factor):
    with pytest.raises(ValueError, match="must be a number"):
        load_numerals(
            _mutated(
                tmp_path,
                lambda raw: raw["languages"]["en"]["multipliers"].update(k=factor),
            )
        )


def test_an_empty_multiplier_table_is_rejected(tmp_path):
    # "8 million" would read as a count of 8 in every language at once.
    def strip(raw):
        for block in raw["languages"].values():
            block["multipliers"] = {}

    with pytest.raises(ValueError, match="no multiplier words at all"):
        load_numerals(_mutated(tmp_path, strip))


def test_no_percent_marker_at_all_is_rejected(tmp_path):
    # An empty alternation matches the EMPTY STRING, which would make every
    # figure a percentage. That scrambles classification rather than widening
    # it, so it fails at start-up instead.
    def strip(raw):
        raw["symbols"]["percent"] = []
        for block in raw["languages"].values():
            block["percent_words"] = []

    with pytest.raises(ValueError, match="no percent symbols or words"):
        load_numerals(_mutated(tmp_path, strip))


def test_no_currency_token_at_all_is_rejected(tmp_path):
    # Same failure in the other direction: with no currency tokens every figure
    # would look like money, and "AED 12" would be a count again.
    def strip(raw):
        raw["symbols"]["currency"] = []
        raw["latin_currency_tokens"] = []
        for block in raw["languages"].values():
            block["currency_words"] = []

    with pytest.raises(ValueError, match="no currency tokens at all"):
        load_numerals(_mutated(tmp_path, strip))


def test_a_percent_word_alone_still_classifies(tmp_path):
    # The symbol and the word branches of the percent alternation are built
    # separately, so each must work without the other.
    numerals = load_numerals(
        _mutated(tmp_path, lambda raw: raw["symbols"].update(percent=[]))
    )
    assert extract_figures("20 percent", numerals)[0].figure.kind == "percent"
    assert extract_figures("20%", numerals)[0].figure.kind == "amount"


def test_a_currency_symbol_alone_still_kills_the_exemption(tmp_path):
    def strip(raw):
        raw["latin_currency_tokens"] = []
        for block in raw["languages"].values():
            block["currency_words"] = []

    numerals = load_numerals(_mutated(tmp_path, strip))
    assert extract_figures("$12", numerals)[0].figure.kind == "amount"
    assert extract_figures("AED 12", numerals)[0].figure.kind == "count"
