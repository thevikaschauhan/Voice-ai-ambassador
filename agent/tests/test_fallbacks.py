"""Composed failure speech loaded from data/fallbacks.yaml.

No framework import here on purpose: the loader depends on yaml and
`ambassador.schemas` only, so these tests run in core-only mode
(`uv sync --no-group voice`) alongside the rest.

The copy is a product artefact - a buyer hears these exact words - so the
assertions pin the strings rather than checking that something non-empty came
back. That is what makes an edit to the data file a visible change here
instead of a silent change on a live call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapter.fallbacks import FallbackCopy, load_fallback_copy

BRIDGE_EN = "Let me be precise about that figure rather than guess."
FALLBACK_EN = (
    "I do not want to quote you anything I cannot confirm. "
    "Let me put you through to one of our ambassadors."
)

LANGUAGES = ("en", "ar", "hi")


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


def _dump(blocks: dict[str, dict[str, str]]) -> str:
    """Hand-written rather than yaml.dump: the fixture should not depend on the
    dumper agreeing with the loader about quoting."""
    lines: list[str] = []
    for kind, entries in blocks.items():
        lines.append(f"{kind}:")
        for language, text in entries.items():
            lines.append(f'  {language}: "{text}"')
    return "\n".join(lines) + "\n"
