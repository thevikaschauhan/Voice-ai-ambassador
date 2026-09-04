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
    env.write_text("LANGUAGE=fr\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LANGUAGE must be one of"):
        config.load_settings(env)


def test_settings_resolve_a_voice_and_an_stt_model_for_every_language(tmp_path):
    """`voice_id` and `stt_model` are hand-keyed on the same set.

    They cannot drift as silently as a plain tuple - a new language also needs a
    new settings field - but a KeyError here is still a dead voice path, and the
    check costs four lines.
    """
    env = tmp_path / ".env"
    env.write_text("STT_MODEL_DEFAULT=some-model\n", encoding="utf-8")
    settings = config.load_settings(env)
    for language in LANGUAGES:
        settings.voice_id(language)
        assert settings.stt_model(language) == "some-model"


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
    """Both language-keyed data files, checked against the Literal.

    fallbacks.yaml is the copy that speaks when the model fails; spoken-forms
    carries each language's currency tokens. A language present in the Literal
    and absent from either file is a defect that only shows up in that language.
    """
    for name in ("fallbacks.yaml", "spoken-forms.yaml"):
        raw = yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))
        if name == "fallbacks.yaml":
            for kind in ("bridge", "fallback"):
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
