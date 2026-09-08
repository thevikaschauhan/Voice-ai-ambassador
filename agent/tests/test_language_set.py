"""One test file for one defect class: a hand-copied list of the languages.

`schemas.Language` is the source of truth. It was restated by hand in four
places, and a single missed copy is not a type error - it is a KeyError or a
silently skipped language on a live call, on the very paths that exist so a turn
never ends in silence. The copies are now derived; these tests are what stops
a new one being written, and what proves each derived collection still lines up
with the Literal.

No framework import here, so this runs in core-only mode alongside the rest.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest
import yaml

from adapter import config, fallbacks
from ambassador import prompts
from ambassador.schemas import Language

LANGUAGES = get_args(Language)

AGENT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = AGENT_DIR.parent / "data"


def test_the_literal_is_not_empty_so_these_tests_can_fail():
    # A guard on the guard: every assertion below is vacuous if get_args() ever
    # returns nothing, and a vacuously green drift test is worse than none.
    assert len(LANGUAGES) >= 2


def test_the_fallback_loader_demands_copy_for_exactly_these_languages():
    assert fallbacks._LANGUAGES == LANGUAGES


def test_config_accepts_every_language(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGUAGE", raising=False)
    env = tmp_path / ".env"
    for language in LANGUAGES:
        env.write_text(f"LANGUAGE={language}\n", encoding="utf-8")
        assert config.load_settings(env).language == language


def test_config_rejects_a_language_the_system_does_not_support(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LANGUAGE=xx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LANGUAGE must be one of"):
        config.load_settings(env)


# The languages with a voice of their own. The other seven deliberately reuse
# the English voice until a voice reference is selected and checked by ear
# (config.py `voice_id`). Listed here so acquiring a real voice is an edit to
# this tuple - a deliberate act - rather than a silent change in behaviour.
LANGUAGES_WITH_THEIR_OWN_VOICE = ("ar", "en", "hi")


def test_settings_resolve_a_voice_and_an_stt_model_for_every_language(tmp_path):
    """The VALUES per language, not merely that the calls do not raise.

    `voice_id` and `deepgram_language` were exhaustive dicts, so a language
    with no entry raised KeyError and the bare CALL was the assertion. Both
    now fall through to an English default, and that change made this test
    unfalsifiable in the same commit that created the case it was watching
    for: `settings.voice_id(language)` on its own can no longer fail for any
    input. Asserting the values is what puts the fallback back under test.

    The fallback itself is the declared choice, not a defect, so it is
    asserted as a choice. What must not happen silently is the opposite: a
    language quietly acquiring or losing a voice.
    """
    env = tmp_path / ".env"
    env.write_text("STT_MODEL_DEFAULT=some-model\n", encoding="utf-8")
    settings = config.load_settings(env)
    own_voice = {
        "en": settings.tts_voice_id_en,
        "ar": settings.tts_voice_id_ar,
        "hi": settings.tts_voice_id_hi,
    }
    assert tuple(sorted(own_voice)) == LANGUAGES_WITH_THEIR_OWN_VOICE
    for language in LANGUAGES:
        assert settings.voice_id(language) == own_voice.get(
            language, settings.tts_voice_id_en
        ), f"{language}: unexpected voice"
        assert settings.stt_model(language) == "some-model"
        # Deepgram's locale. `en-US` for English and the bare two-letter code
        # for everything else - which for the seven new codes is a GUESS the
        # method's own docstring warns against ("settled by listening to real
        # recordings, not by guessing a locale string here"). Pinned rather
        # than left unasserted so that settling one is a visible change and
        # this comment is what the author finds when they do.
        assert settings.deepgram_language(language) == (
            "en-US" if language == "en" else language
        ), f"{language}: unexpected Deepgram locale"


def test_every_language_has_a_prompt_name():
    assert set(prompts.LANGUAGE_NAMES) == set(LANGUAGES)


def test_a_language_with_no_prompt_name_fails_at_import_not_on_the_call():
    """The check that runs when prompts.py is imported, exercised directly.

    Without it the miss surfaces as a KeyError inside `build_ambassador_prompt`,
    which runs at session start on a live call.
    """
    with pytest.raises(RuntimeError, match="LANGUAGE_NAMES is missing a name"):
        prompts._require_every_language_named({LANGUAGES[0]: "English"})


def test_the_shipped_language_names_pass_their_own_check():
    prompts._require_every_language_named(prompts.LANGUAGE_NAMES)


def test_the_prompt_renders_for_every_language():
    for language in LANGUAGES:
        rendered = prompts.build_ambassador_prompt(
            "INVENTORY",
            language,
            system_confirms_budget=False,
            system_confirms_project=False,
        )
        assert prompts.LANGUAGE_NAMES[language] in rendered


def test_the_data_files_carry_a_block_for_every_language():
    """Every language-keyed data file, checked against the Literal.

    fallbacks.yaml is the copy that speaks when the model fails; spoken-forms
    carries each language's currency tokens. A language present in the Literal
    and absent from either file is a defect that only shows up in that language.

    farewells.yaml joined this set after the language-switch review, and it is
    the reason the set is worth having. It was NOT updated when `Language` grew
    to ten codes, and nothing noticed, because `detects()` reads it with
    `.get()` and an absent language simply reads as "no closing phrases here".
    That fails closed in the safe direction for a language chosen at start-up
    and in a very unsafe one once the language can change MID-CALL: the call
    silently loses the buyer's only way to end it, and on a hosted call with no
    duration cap nothing else ends it either (reproduced at 3ab4928). An empty
    `[]` is still the correct CONTENT - a guessed phrase list would hang up on
    a live buyer - so what this asserts is that the slot is DECLARED, which is
    what makes the gap visible to a reviewer instead of to a caller.
    """
    for name in ("fallbacks.yaml", "spoken-forms.yaml", "farewells.yaml"):
        raw = yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))
        if name == "fallbacks.yaml":
            for kind in ("bridge", "fallback"):
                assert set(raw[kind]) == set(LANGUAGES), f"{name}:{kind}"
        elif name == "farewells.yaml":
            # `speech` is the agent's own closing line and falls back to
            # English by design (farewell.py), so only the two the DETECTOR
            # reads are required per language.
            for kind in ("phrases", "courtesies"):
                assert set(raw[kind]) == set(LANGUAGES), f"{name}:{kind}"
        else:
            assert set(raw) == set(LANGUAGES), name


# --- the tripwire ---------------------------------------------------------

# A comma-separated run of quoted two-letter codes: the shape every one of the
# four hand-copies took.
_RUN = re.compile(r"""(?:['"][a-z]{2}['"]\s*,\s*)+['"][a-z]{2}['"]""")
_CODE = re.compile(r"""['"]([a-z]{2})['"]""")

# schemas.py IS the definition, and this file builds the set it searches for.
_ALLOWED_TO_SPELL_IT_OUT = {"schemas.py", Path(__file__).name}


def test_no_module_restates_the_language_set():
    """Fail on a NEW hand-copy, not just on the four that were found.

    The four known copies are derived now, so an equality test against each one
    passes forever while a fifth copy is written somewhere else. This is the
    test that notices the fifth.
    """
    sources = [
        path
        for directory in ("src", "tests", "spikes")
        for path in (AGENT_DIR / directory).glob("**/*.py")
    ]
    assert sources, "found no Python to scan, so this test cannot fail"
    offenders: list[str] = []
    for path in sorted(sources):
        if path.name in _ALLOWED_TO_SPELL_IT_OUT:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for run in _RUN.findall(line):
                if set(_CODE.findall(run)) == set(LANGUAGES):
                    offenders.append(f"{path.relative_to(AGENT_DIR)}:{number}: {run}")
    assert not offenders, (
        "the language set is spelled out by hand here; derive it with "
        "typing.get_args(schemas.Language) instead:\n  " + "\n  ".join(offenders)
    )


def test_the_stopword_file_covers_exactly_these_languages():
    """`data/stopwords.yaml` is read per turn to build the retrieval query. A
    language missing from it silently keeps every stopword, which ANDs a
    spoken sentence into a query that matches nothing - the defect this file
    exists to stop, in its retrieval form."""
    stopwords = yaml.safe_load((DATA_DIR / "stopwords.yaml").read_text("utf-8"))
    assert set(stopwords) == set(LANGUAGES)
    for language, words in stopwords.items():
        assert words, f"{language} has an empty stopword list"
        # YAML 1.1 reads bare `on`, `no`, `yes` and `off` as booleans, and an
        # unquoted `on` in the English list loaded as True and crashed the
        # loader. Every entry is quoted; this is what keeps it that way.
        assert all(isinstance(word, str) for word in words), (
            f"{language} has a non-string entry; quote the YAML"
        )
