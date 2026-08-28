"""Composed failure speech loaded from data/fallbacks.yaml.

No framework import here on purpose: the loader depends on yaml and
`ambassador.schemas` only, so these tests run in core-only mode
(`uv sync --no-group voice`) alongside the rest. The compose check below reaches
for `guardrails.pipeline.process_sentence` rather than the adapter's
`SentenceGuard` for the same reason - it is the same single public path, minus
the framework import.

The copy is a product artefact - a buyer hears these exact words - so the
assertions pin the strings rather than checking that something non-empty came
back. That is what makes an edit to the data file a visible change here
instead of a silent change on a live call.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from adapter.fallbacks import FallbackCopy, load_fallback_copy
from ambassador.guardrails.pipeline import process_sentence
from ambassador.schemas import Language, SpeakableText

BRIDGE_EN = "Let me be precise about that figure rather than guess."
FALLBACK_EN = (
    "I do not want to quote you anything I cannot confirm. "
    "Let me put you through to one of our ambassadors."
)

# Derived, not restated: see tests/test_language_set.py.
LANGUAGES = get_args(Language)


@pytest.fixture(scope="module")
def copy() -> FallbackCopy:
    return load_fallback_copy()


def test_the_shipped_copy_is_exactly_what_the_interception_speaks(copy):
    assert copy.bridge["en"] == BRIDGE_EN
    assert copy.fallback["en"] == FALLBACK_EN


def test_every_language_carries_both_recoveries(copy):
    """AGENTS.md: a turn never ends in silence. ar/hi still hold the English
    copy under a VERIFY: marker in the data file - a stand-in that speaks beats
    a blank that does not, and the marker is what stops it shipping."""
    for language in LANGUAGES:
        assert copy.bridge[language].strip()
        assert copy.fallback[language].strip()


def test_every_language_composes_through_the_guardrail_pipeline(
    copy, allowed, patterns, forms
):
    """The recovery copy is held to the invariant it exists to uphold.

    `SentenceGuard.compose` raises rather than speaking copy that fails our own
    guardrails, and `llm_node` catches that and speaks the raw copy instead. So
    a violation in ar or hi is not a silent turn - it is a turn that bypasses
    the guardrails. Every language is checked here, not only the one the build
    team can read.
    """
    for language in LANGUAGES:
        for kind, table in (("bridge", copy.bridge), ("fallback", copy.fallback)):
            result = process_sentence(
                table[language], language, allowed, patterns, forms
            )
            assert isinstance(result, SpeakableText), (
                f"{kind} copy for {language!r} fails our own guardrails: {result}"
            )


def test_the_two_recoveries_are_different_copy(copy):
    """They are separate claims (docs/01-): the bridge covers a seam mid-turn,
    the fallback IS the reply and hands the buyer to a human."""
    assert copy.bridge["en"] != copy.fallback["en"]
    assert "ambassador" in copy.fallback["en"]


@pytest.mark.parametrize("kind", ["bridge", "fallback"])
@pytest.mark.parametrize("language", LANGUAGES)
def test_an_empty_string_fails_the_load_rather_than_the_call(
    tmp_path: Path, kind: str, language: str
):
    """An empty entry is a silent turn. It has to fail in front of whoever
    edited the file, not as a mid-call blank."""
    blocks = {
        "bridge": dict.fromkeys(LANGUAGES, BRIDGE_EN),
        "fallback": dict.fromkeys(LANGUAGES, FALLBACK_EN),
    }
    blocks[kind][language] = ""
    path = tmp_path / "fallbacks.yaml"
    path.write_text(_dump(blocks), encoding="utf-8")

    with pytest.raises(ValueError, match=f"'{kind}' has no copy for '{language}'"):
        load_fallback_copy(path)


def test_a_missing_block_fails_the_load(tmp_path: Path):
    path = tmp_path / "fallbacks.yaml"
    path.write_text(
        _dump({"bridge": dict.fromkeys(LANGUAGES, BRIDGE_EN)}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="'fallback' has no copy for 'en'"):
        load_fallback_copy(path)


def test_an_empty_file_fails_the_load(tmp_path: Path):
    path = tmp_path / "fallbacks.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="'bridge' has no copy for 'en'"):
        load_fallback_copy(path)


# --- shapes YAML makes easy to write by accident --------------------------
#
# The loader used to coerce whatever it found with str(), so a value that was
# not a string became speech: `en: yes` is a boolean and shipped as the spoken
# word "True", `en: 985000` shipped as "985000", and a falsy scalar like
# `en: no` coerced to "" and was then MISreported as missing copy. A document or
# a block of the wrong shape raised a bare AttributeError from inside the
# loader. All four now name the file, the block, the language and the type.


@pytest.mark.parametrize(
    "written,offending_type",
    [
        pytest.param("yes", "bool", id="bare yes is a boolean"),
        pytest.param("no", "bool", id="bare no is a falsy boolean"),
        pytest.param("on", "bool", id="bare on is a boolean"),
        pytest.param("985000", "int", id="bare digits are a number"),
        pytest.param("1.5", "float", id="a bare decimal is a number"),
        pytest.param("[a, b]", "list", id="a list is not a sentence"),
    ],
)
def test_a_value_that_is_not_text_is_rejected_by_type(
    tmp_path: Path, written: str, offending_type: str
):
    path = tmp_path / "fallbacks.yaml"
    path.write_text(_raw(bridge_en=written, quote_bridge_en=False), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_fallback_copy(path)

    message = str(excinfo.value)
    assert "fallbacks.yaml" in message
    assert "'bridge'" in message
    assert "'en'" in message
    assert f"is a {offending_type}" in message
    # The falsy ones used to be reported as absent copy, which sends whoever
    # edited the file looking for a missing line that is right there.
    assert "has no copy" not in message


def test_a_document_that_is_not_a_mapping_fails_with_a_message(tmp_path: Path):
    path = tmp_path / "fallbacks.yaml"
    path.write_text("- bridge\n- fallback\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping of block name"):
        load_fallback_copy(path)


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("bridge: just a sentence\n", id="scalar"),
        pytest.param("bridge:\n  - en\n  - ar\n", id="list"),
    ],
)
def test_a_block_that_is_not_a_mapping_fails_with_a_message(tmp_path: Path, block: str):
    path = tmp_path / "fallbacks.yaml"
    path.write_text(block, encoding="utf-8")

    with pytest.raises(
        ValueError, match="'bridge' must be a mapping of language to copy"
    ):
        load_fallback_copy(path)


def _dump(blocks: dict[str, dict[str, str]]) -> str:
    """Hand-written rather than yaml.dump: the fixture should not depend on the
    dumper agreeing with the loader about quoting."""
    lines: list[str] = []
    for kind, entries in blocks.items():
        lines.append(f"{kind}:")
        for language, text in entries.items():
            lines.append(f'  {language}: "{text}"')
    return "\n".join(lines) + "\n"


def _raw(*, bridge_en: str, quote_bridge_en: bool) -> str:
    """A complete, otherwise-valid file with one entry written verbatim.

    Unquoted on purpose: the whole point is what YAML does to a bare word.
    """
    value = f'"{bridge_en}"' if quote_bridge_en else bridge_en
    lines = ["bridge:", f"  en: {value}"]
    lines += [f'  {language}: "{BRIDGE_EN}"' for language in LANGUAGES[1:]]
    lines.append("fallback:")
    lines += [f'  {language}: "{FALLBACK_EN}"' for language in LANGUAGES]
    return "\n".join(lines) + "\n"
