"""Pronunciation respelling on the way to the synthesiser.

`data/lexicon.yaml` was authored, documented, and read by nothing, so every
respelling in it was inert and "Binghatti" was synthesised from its literal
spelling. Two properties carry the weight here:

  the client's name is respelled, including when the stream happens to split
  across it, because that failure would be silent and the docs call
  mispronouncing it in their own boardroom unrecoverable;

  nothing is held back on the latency path, because TTS first audio is one of
  the two largest remaining components of the budget.

No framework import, so this runs in core-only mode with the rest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from adapter.lexicon import Lexicon, load_lexicon, respell_stream


async def astream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


async def collect(source) -> list[str]:
    return [chunk async for chunk in source]


def write(tmp_path, body: str):
    path = tmp_path / "lexicon.yaml"
    path.write_text(body, encoding="utf-8")
    return path


ONE_TERM = """
- term: Binghatti
  respell:
    en: "bin-GAH-tee"
"""


# --- the shipped file -----------------------------------------------------


def test_the_clients_own_name_is_respelled_in_english():
    lexicon = load_lexicon()
    spoken = lexicon.apply("Binghatti Skyrise is in Business Bay.", "en")
    assert "bin-GAH-tee" in spoken
    assert "Binghatti" not in spoken


def test_a_language_with_no_authored_respellings_is_left_alone():
    """Untouched is exactly the old behaviour, so an unauthored language is no
    worse off than before this module existed - and specifically is never
    handed an English respelling to read."""
    lexicon = load_lexicon()
    original = "Binghatti Skyrise"
    for language in ("ar", "hi"):
        if language in lexicon.languages_covered():
            continue
        assert lexicon.apply(original, language) == original


def test_the_longest_matching_term_wins():
    """Otherwise a bare "Jumeirah" is rewritten inside "Jumeirah Village
    Circle" and the rest of the phrase is stranded beside a respelling."""
    spoken = load_lexicon().apply("The tower is in Jumeirah Village Circle.", "en")
    assert "joo-MAY-rah Village Circle" in spoken


def test_a_term_written_without_its_spaces_still_matches():
    """The docs write "Jacob&Co" and a model will too."""
    lexicon = load_lexicon()
    for surface in ("Jacob & Co", "Jacob&Co", "Jacob  &  Co"):
        assert "JAY-kob and ko" in lexicon.apply(f"The {surface} residences.", "en")


def test_a_term_inside_a_longer_word_is_not_respelled():
    """`(?<!\\w)`/`(?!\\w)` rather than a bare substring: respelling the "AED"
    inside a word would corrupt it."""
    assert load_lexicon().apply("PAEDIATRIC", "en") == "PAEDIATRIC"


# --- the streaming path ---------------------------------------------------


async def test_a_term_split_across_two_chunks_is_still_respelled(tmp_path):
    """The silent failure this buffering exists to prevent.

    Today's chunks are whole sentences so this cannot happen, which is exactly
    why it needs a test: the day something upstream re-chunks more finely,
    nothing else would notice.
    """
    lexicon = load_lexicon(write(tmp_path, ONE_TERM))
    out = await collect(
        respell_stream(astream("Bing", "hatti Skyrise."), lexicon, "en")
    )
    joined = "".join(out)
    assert "bin-GAH-tee" in joined
    assert "Binghatti" not in joined


async def test_a_complete_sentence_is_emitted_without_waiting_for_more(tmp_path):
    """The latency guarantee. Buffering must not hold the first sentence.

    If this ever fails, TTS first audio is being delayed by a full extra
    sentence on every turn, which the budget cannot absorb.
    """
    lexicon = load_lexicon(write(tmp_path, ONE_TERM))

    emitted: list[str] = []
    sent_second = False

    async def source() -> AsyncIterator[str]:
        yield "Binghatti Skyrise is ready. "
        # Only reached after the consumer has taken the first chunk, so if the
        # first sentence had been held back, `emitted` would still be empty.
        nonlocal sent_second
        sent_second = True
        assert emitted, "the first sentence was held back behind the second"
        yield "Handover is soon."

    async for chunk in respell_stream(source(), lexicon, "en"):
        emitted.append(chunk)

    assert sent_second
    assert "bin-GAH-tee" in "".join(emitted)


async def test_the_text_is_preserved_apart_from_the_respelling(tmp_path):
    """Whitespace between sentences included.

    Cutting at the boundary rather than splitting on it is what keeps this
    true; splitting consumes the separator and runs two sentences together.
    """
    lexicon = load_lexicon(write(tmp_path, ONE_TERM))
    original = "Handover is soon.  The tower is ready. Ask us."
    out = "".join(await collect(respell_stream(astream(original), lexicon, "en")))
    assert out == original


async def test_an_unauthored_language_is_not_even_buffered(tmp_path):
    """The source object is handed back as-is, so a language nobody has
    authored pays nothing at all."""
    lexicon = load_lexicon(write(tmp_path, ONE_TERM))
    source = astream("Binghatti Skyrise.")
    assert respell_stream(source, lexicon, "ar") is source


async def test_a_stream_ending_without_terminal_punctuation_is_still_flushed(
    tmp_path,
):
    """A trailing fragment must not be swallowed - that would be a turn ending
    in partial silence, which AGENTS.md does not permit."""
    lexicon = load_lexicon(write(tmp_path, ONE_TERM))
    out = "".join(await collect(respell_stream(astream("Binghatti"), lexicon, "en")))
    assert out == "bin-GAH-tee"


# --- the loader -----------------------------------------------------------


def test_a_bare_string_respelling_is_refused_rather_than_assumed_english(tmp_path):
    """The old file shape. Silently treating it as English is how an English
    respelling reaches an Arabic voice."""
    path = write(tmp_path, '- term: Binghatti\n  respell: "bin-GAH-tee"\n')
    with pytest.raises(ValueError, match="map a language code"):
        load_lexicon(path)


def test_an_empty_respelling_is_refused(tmp_path):
    path = write(tmp_path, '- term: Binghatti\n  respell:\n    en: "   "\n')
    with pytest.raises(ValueError, match="empty or not text"):
        load_lexicon(path)


def test_an_entry_with_no_term_is_refused(tmp_path):
    with pytest.raises(ValueError, match="needs a 'term'"):
        load_lexicon(write(tmp_path, '- respell:\n    en: "bin-GAH-tee"\n'))


def test_a_term_with_no_respellings_at_all_is_allowed(tmp_path):
    """`arpabet`-only entries are legitimate: Fish takes phonemes for English,
    and a term may be waiting on native review for everything else."""
    lexicon = load_lexicon(write(tmp_path, "- term: Binghatti\n  arpabet: null\n"))
    assert lexicon.languages_covered() == frozenset()
    assert lexicon.apply("Binghatti", "en") == "Binghatti"


def test_coverage_reports_only_languages_with_entries(tmp_path):
    path = write(
        tmp_path,
        '- term: Binghatti\n  respell:\n    en: "bin-GAH-tee"\n    ar: "بن غاطي"\n',
    )
    assert load_lexicon(path).languages_covered() == frozenset({"en", "ar"})


def test_an_empty_lexicon_is_not_an_error(tmp_path):
    assert load_lexicon(write(tmp_path, "")) == Lexicon(by_language={})


def test_a_respelling_is_substituted_literally_not_as_a_regex_template(tmp_path):
    r"""`re.sub` reads a replacement string as a template.

    A backslash in a respelling is an escape and `\1` is a group reference;
    both raise `re.PatternError`. This runs inside `tts_node`, so that raise is
    a turn with no audio, and the values likeliest to contain a backslash are
    exactly the native-authored ones this file exists to receive.
    """
    for respelling in (r"bin\GAH-tee", r"bin-\1-tee", r"bin\\GAH", r"a\g<0>b"):
        # YAML single-quoted scalars take a backslash literally, so the value
        # in the file is exactly `respelling`. Python's repr() would escape it
        # and test something else.
        quoted = "'" + respelling.replace("'", "''") + "'"
        path = write(
            tmp_path, "- term: Binghatti\n  respell:\n    en: " + quoted + "\n"
        )
        spoken = load_lexicon(path).apply("Binghatti Skyrise", "en")
        assert spoken == f"{respelling} Skyrise", respelling
