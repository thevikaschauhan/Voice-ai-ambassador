"""The preroll loader.

`data/prerolls.yaml` was the third data file in this repository to be
configuration in appearance and a document in fact. The direct cost of the
missing loader was small - a latency mask nobody played - but the indirect one
was not: its `VERIFY:` markers for Arabic and Hindi could not reach the
native-reviewer packet, because the packet is generated from the loaders the
runtime uses. So the copy nobody here may write was also never being asked for.

These tests hold the two things that keep it honest: the shapes a person gets
wrong while editing fail at load, and everything else degrades to no filler
rather than to somebody else's language.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapter.prerolls import load_prerolls


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "prerolls.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_file_carries_english_and_owes_the_other_two():
    """The state of the repository, asserted rather than assumed. Arabic and
    Hindi are empty under a `VERIFY:` marker, and this is what makes that
    absence visible to the packet instead of invisible in a file."""
    prerolls = load_prerolls()
    assert prerolls.languages_covered() == frozenset({"en"})
    assert prerolls.for_language("en") == (
        "Let me look at the collection for you.",
        "One moment while I check that.",
    )


def test_an_unauthored_language_gets_nothing_not_english(tmp_path: Path):
    """The failure direction, and the one place this loader deliberately
    differs from the disclosure's. A notice the buyer may not read still beats
    no notice; an English filler dropped into an Arabic call is a seam the
    buyer hears rather than one it hides."""
    prerolls = load_prerolls(write(tmp_path, 'en:\n  - "One moment."\nar: []\n'))
    assert prerolls.for_language("en") == ("One moment.",)
    # Empty list and absent key both, so neither shape borrows the English.
    assert prerolls.for_language("ar") == ()
    assert prerolls.for_language("hi") == ()
    assert prerolls.languages_covered() == frozenset({"en"})


def test_an_empty_file_loads_to_no_prerolls_anywhere(tmp_path: Path):
    """A preroll is a nicety, so absence is a valid state - unlike the
    fallback copy, whose absence would be a silent turn."""
    prerolls = load_prerolls(write(tmp_path, ""))
    assert prerolls.languages_covered() == frozenset()


def test_a_misspelled_language_key_is_refused(tmp_path: Path):
    """Otherwise it is a list of copy nobody will ever play, and nothing else
    in the system would notice - which is the failure this module exists to
    end, reproduced one key deeper."""
    with pytest.raises(ValueError, match="'ur' is not a language"):
        load_prerolls(write(tmp_path, 'en: []\nur:\n  - "ایک لمحہ"\n'))


def test_a_non_string_line_is_refused(tmp_path: Path):
    """YAML reads bare yes/no/on/off as booleans and bare digits as numbers,
    and these strings are spoken to a buyer verbatim."""
    with pytest.raises(ValueError, match="empty or not text"):
        load_prerolls(write(tmp_path, "en:\n  - no\n"))


def test_a_blank_line_is_refused(tmp_path: Path):
    """A blank entry would play as silence on the one turn the buyer is
    already waiting through."""
    with pytest.raises(ValueError, match="empty or not text"):
        load_prerolls(write(tmp_path, 'en:\n  - "   "\n'))


def test_a_language_mapped_to_a_scalar_is_refused(tmp_path: Path):
    """A single line written without its dash. It would otherwise reach
    `.strip()` on a str and load as a list of characters."""
    with pytest.raises(ValueError, match="must be a list of lines"):
        load_prerolls(write(tmp_path, 'en: "One moment."\n'))


def test_a_document_of_the_wrong_shape_fails_with_a_message(tmp_path: Path):
    with pytest.raises(ValueError, match="must be a mapping of language"):
        load_prerolls(write(tmp_path, '- "One moment."\n'))


def test_the_loaded_copy_cannot_be_mutated_by_a_caller():
    """Tuples, so a turn that picks a line cannot pop it out of the table for
    every later turn in the process."""
    prerolls = load_prerolls()
    assert isinstance(prerolls.for_language("en"), tuple)
