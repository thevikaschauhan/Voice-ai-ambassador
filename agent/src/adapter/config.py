"""Adapter configuration: the one place environment enters the system.

The core (`src/ambassador/`) reads no environment at all - that is what makes
its behaviour provable from its inputs (AGENTS.md, coding conventions). Every
vendor credential and every demo toggle is resolved here, once, at process
start, and handed to the adapter as a frozen value.

Secrets are never printed. `Settings.__repr__` and `Settings.redacted()` mask
every credential-bearing field, so a settings object can be logged or dropped
into a traceback without leaking a key.

Two environment variables are read outside `Settings`, both by `events.py`,
because they configure a sink rather than the session:

  AMBASSADOR_EVENT_LOG      path to a second, file-based event sink. It
                            receives exactly the stream stdout receives, which
                            means it is redacted unless the flag below is set.
  AMBASSADOR_EVENT_VERBOSE  DEV ONLY. Restores full emission: buyer utterance
                            text and complete lead briefs reach stdout and the
                            file sink. Never set it for a demo or a
                            deployment - docs/02- and docs/03- say PII does not
                            land in an emitted or durable stream, and this flag
                            is the one way to break that. The in-memory
                            `TurnRecord`s carry the full text either way, so
                            the ambassador view and the audit lose nothing when
                            it is off.

Two more are read by `events_bridge.py`, for the same reason - they configure a
sink, not the session:

  AMBASSADOR_BRIDGE_HANDSHAKE  path to a 0600 file carrying the bridge's host,
                               port and per-session token. Its presence is what
                               enables the bridge at all, and it is also how the
                               local consumer finds it, so a bridge is never
                               listening without a reader that was told about
                               it. The bridge serves the UNREDACTED records to
                               that one local process (docs/03-, "the one
                               surface that is not redacted").
  AMBASSADOR_BRIDGE_PORT       optional fixed port. Default 0 - ephemeral -
                               because the handshake file carries the real one
                               and a fixed port is one more thing a scanning
                               page can guess.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Final, Literal, get_args

from ambassador.schemas import Language

AGENT_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = AGENT_DIR / ".env"

GuardrailMode = Literal["enforce", "warn"]
PromptMode = Literal["ambassador", "naive"]

# Derived from the Literal rather than restated, so LANGUAGE cannot start
# rejecting a language the rest of the system already supports.
_LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)

# Words that make a field name credential-bearing. Matched against the name's
# underscore-separated parts, not as substrings, so "monkey" is not a "key".
#
# Classified by NAME rather than listed by hand, because the hand-written list
# went stale the day a credential was added: DEEPGRAM_API_KEY (ADR-017) was
# never appended to it, so the key printed in full through `repr()` and
# `redacted()` for as long as it existed, while the test guarding this passed,
# because that test enumerated the credentials that existed when it was
# written. A rule covers the field nobody remembers to add; a list covers the
# fields someone already thought of.
_CREDENTIAL_WORDS: Final = frozenset(
    {"key", "secret", "token", "password", "credential"}
)


def _is_credential(field_name: str) -> bool:
    """True when the name says the value is a credential.

    Plurals count: `api_keys` is as much a credential as `api_key`, and the
    first version of this rule missed it because it compared whole parts
    against singular words only.

    A part that merely CONTAINS a credential word does not count, so `monkey`
    is not a `key` and an operator can still read their own configuration out
    of a log. That leaves one gap on purpose - a run-together name like
    `apikey` - and `test_no_credential_looking_field_escapes_classification`
    is the guard for it: it scans the dataclass with a deliberately looser
    substring rule and fails the build rather than letting the value leak.
    """
    parts = set(field_name.split("_"))
    return bool(_CREDENTIAL_WORDS & (parts | {p.removesuffix("s") for p in parts}))


_MASK = "<set>"
_UNSET = "<unset>"


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for agent/.env.

    Deliberately not python-dotenv: this reads one flat file with no
    interpolation, and AGENTS.md asks for a stated reason behind every
    dependency. Trailing ` # comment` is stripped, matching the format the
    day-1 smoke spike already established for this same file.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split("#")[0].strip()
    return values


# Provisional Fish voice ids, one per shipped language (docs/voice-shortlist.md).
#
# PROVISIONAL, and the word is load-bearing: these are the top register match in
# each language's shortlist, not a choice anybody has made by ear. The point of
# that page is that the CLIENT chooses, and two candidates per language still go
# to the meeting. What these defaults settle is only the thing nobody wants to
# decide by accident - without them the session falls through to
# `fishaudio.tts.DEFAULT_VOICE_ID`, which is an English voice nobody selected,
# used for Arabic and Hindi as well.
#
# The human decided on 2026-09-01 that a voice without Fish's `licensed` flag
# may ship for the POC; no voice in en/ar/hi carries that flag, and the ar/hi
# candidates are community uploads of unverified provenance. What the paid tier
# grants for a public-library voice is still `VERIFY:` for anything
# client-facing, and it is tracked on that page rather than resolved here.
PROVISIONAL_VOICE_ID_EN = "536d3a5e000945adb7038665781a4aca"  # "Ethan", Fish Official
PROVISIONAL_VOICE_ID_AR = "10c5c2a37a284a81bb0cf3c53955d795"  # Gulf-accented, community
PROVISIONAL_VOICE_ID_HI = "6209a5682085409fa935f901f0bce950"  # "neel", community


def _resolve(file_values: dict[str, str], key: str, default: str = "") -> str:
    """Process environment wins over the file, so a one-off run can override
    without editing .env (`GUARDRAIL_MODE=warn uv run ...`)."""
    from_process = os.environ.get(key)
    if from_process:
        return from_process.strip()
    return file_values.get(key, default)


def _resolve_bool(file_values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = _resolve(file_values, key, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # LiveKit transport (ADR-005)
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str

    # LLM - Qwen 3.7 Flash via OpenRouter (ADR-016)
    openrouter_api_key: str
    llm_model: str
    llm_base_url: str
    llm_thinking: str
    brief_model: str

    # STT - whole-utterance via OpenRouter (ADR-015) or streaming via Deepgram.
    # The two differ on where transcription lands in the turn, not just on
    # speed; see stt_factory.
    stt_provider: str
    stt_model_default: str
    stt_model_ar: str
    stt_enabled: bool
    deepgram_api_key: str
    deepgram_model: str

    # TTS - Fish Audio (ADR-014)
    fish_api_key: str
    fish_tts_model: str
    tts_voice_id_en: str
    tts_voice_id_ar: str
    tts_voice_id_hi: str

    # Demo toggles
    guardrail_mode: GuardrailMode
    prompt_mode: PromptMode
    demo_mode: bool
    language: Language
    # Opens a call in a language with no native-authored disclosure, falling
    # back to the English one. docs/04- argues for demonstrating Arabic
    # degrading gracefully rather than avoiding Arabic, and this is the switch
    # that permits it. Off by default: the disclosure is the one thing a call
    # may not open without.
    allow_uncertified_language: bool

    @property
    def thinking_disabled(self) -> bool:
        """ADR-016's trap. Anything other than an explicit 'on' keeps thinking
        off, because the failure mode of guessing wrong is silent seconds of
        latency before first audio."""
        return self.llm_thinking.lower() != "on"

    def voice_id(self, language: Language) -> str:
        return {
            "en": self.tts_voice_id_en,
            "ar": self.tts_voice_id_ar,
            "hi": self.tts_voice_id_hi,
        }[language]

    def stt_model(self, language: Language) -> str:
        """Per-language STT routing (ADR-015). The Arabic slot is decided by the
        day-0 head-to-head; until it is set, Arabic falls back to the default."""
        if language == "ar" and self.stt_model_ar:
            return self.stt_model_ar
        return self.stt_model_default

    def deepgram_language(self, language: Language) -> str:
        """Our two-letter code as the locale Deepgram expects.

        Arabic is deliberately the bare `ar` rather than a country locale: the
        dialect question (Gulf, Egyptian, Levantine, not MSA) is the build's
        highest risk (A6) and is settled by listening to real recordings, not
        by guessing a locale string here.
        """
        return {"en": "en-US", "ar": "ar", "hi": "hi"}[language]

    def redacted(self) -> dict[str, object]:
        """Loggable view: secrets collapse to a presence flag, never a value."""
        out: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if _is_credential(field.name):
                out[field.name] = _MASK if value else _UNSET
            else:
                out[field.name] = value
        return out

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self.redacted().items())
        return f"Settings({inner})"

    def missing_for_voice(self) -> list[str]:
        """Credentials the voice path cannot start without. Reported by name
        only - the check never echoes a value.

        The recogniser's credential depends on which recogniser is selected, so
        it is conditional rather than always required. Omitting it did not make
        the failure silent - the Deepgram plugin raises when it is constructed -
        but it moved the failure from this preflight, which names everything
        missing at once, to session start, which names one thing and only after
        the operator has begun a demo.
        """
        required = {
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "FISH_API_KEY": self.fish_api_key,
        }
        if self.stt_enabled and self.stt_provider.lower() == "deepgram":
            required["DEEPGRAM_API_KEY"] = self.deepgram_api_key
        return [name for name, value in required.items() if not value]


# Remedies named per credential, because "missing DEEPGRAM_API_KEY" on a
# checkout that worked yesterday is a question, not an answer. Keyed by variable
# so a second conditional credential gets the same treatment rather than a
# second special case.
_REMEDIES: Final[dict[str, str]] = {
    "DEEPGRAM_API_KEY": (
        "Deepgram nova-3 is the recogniser (ADR-017: 258-327ms after endpoint "
        "against the whole-utterance path's p50 1081ms, and it is the only path "
        'that hears "Binghatti" and returns figures as digits). Either add a '
        "key from console.deepgram.com, or set STT_PROVIDER=openrouter to use "
        "the slower whole-utterance path on your existing OPENROUTER_API_KEY, "
        "or set STT_ENABLED= to run text mode with no recogniser at all."
    ),
}


def missing_credentials_error(missing: list[str]) -> str:
    """The startup message for a voice path that cannot run.

    A bare list of variable names is diagnosable only by whoever already knows
    why each one is needed. This names the missing variable, says what it is
    for, and gives every way out - including the two that need no new account -
    so a keyless checkout is a five-second fix rather than a bug report.

    Composed here rather than at the raise site because `entrypoint` needs
    transport, a worker process and real credentials, so nothing written there
    is testable, and an error message nobody can test is one nobody notices
    going stale.
    """
    lines = ["missing credentials for the voice path: " + ", ".join(missing)]
    lines += [f"  {name}: {_REMEDIES[name]}" for name in missing if name in _REMEDIES]
    lines.append(
        "Set them in agent/.env (see agent/.env.example) or in the environment."
    )
    return "\n".join(lines)


def load_settings(env_path: Path | None = None) -> Settings:
    file_values = parse_env_file(env_path or ENV_PATH)

    guardrail_mode = _resolve(file_values, "GUARDRAIL_MODE", "enforce")
    if guardrail_mode not in ("enforce", "warn"):
        raise ValueError(
            f"GUARDRAIL_MODE must be 'enforce' or 'warn', got {guardrail_mode!r}"
        )

    prompt_mode = _resolve(file_values, "PROMPT_MODE", "ambassador")
    if prompt_mode not in ("ambassador", "naive"):
        raise ValueError(
            f"PROMPT_MODE must be 'ambassador' or 'naive', got {prompt_mode!r}"
        )

    language = _resolve(file_values, "LANGUAGE", "en")
    if language not in _LANGUAGES:
        raise ValueError(
            f"LANGUAGE must be one of {'/'.join(_LANGUAGES)}, got {language!r}"
        )

    return Settings(
        livekit_url=_resolve(file_values, "LIVEKIT_URL"),
        livekit_api_key=_resolve(file_values, "LIVEKIT_API_KEY"),
        livekit_api_secret=_resolve(file_values, "LIVEKIT_API_SECRET"),
        openrouter_api_key=_resolve(file_values, "OPENROUTER_API_KEY"),
        llm_model=_resolve(file_values, "LLM_MODEL", "qwen/qwen3.7-flash"),
        llm_base_url=_resolve(
            file_values, "LLM_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        llm_thinking=_resolve(file_values, "LLM_THINKING", "off"),
        brief_model=_resolve(file_values, "BRIEF_MODEL", "qwen/qwen3.7-flash"),
        # ADR-017: Deepgram nova-3 IS the recogniser. It defaulted to
        # "openrouter" - the path ADR-017 retired - so a checkout with no
        # STT_PROVIDER set ran it: p50 1081ms after endpoint against 258-327ms,
        # "Binghatti" heard as "Bint Jbeil", and figures as words that ADR-011's
        # deterministic confirmation cannot parse. The retired path stays
        # selectable, which is what the ADR decided; it is just not what you
        # get by saying nothing.
        stt_provider=_resolve(file_values, "STT_PROVIDER", "deepgram"),
        stt_model_default=_resolve(
            file_values, "STT_MODEL_DEFAULT", "qwen/qwen3-asr-1.7b"
        ),
        stt_model_ar=_resolve(file_values, "STT_MODEL_AR"),
        deepgram_api_key=_resolve(file_values, "DEEPGRAM_API_KEY"),
        deepgram_model=_resolve(file_values, "DEEPGRAM_MODEL", "nova-3"),
        # Off by default: OpenRouter rejects audio requests under a $0.50
        # balance (AGENTS.md project learnings, 2026-08-27), and the agent must
        # stay runnable in text mode without it.
        stt_enabled=_resolve_bool(file_values, "STT_ENABLED", default=False),
        fish_api_key=_resolve(file_values, "FISH_API_KEY"),
        fish_tts_model=_resolve(file_values, "FISH_TTS_MODEL", "s2.1-pro"),
        # These defaults have to be repeated in agent/.env.example, not left
        # blank there. `parse_env_file` records a bare `KEY=` as an empty
        # STRING, and an empty string is a value: `_resolve` returns it and the
        # default below never runs. So an example shipping `TTS_VOICE_ID_EN=`
        # would silently switch the default off for every operator who copied
        # it - the same drift ADR-017's default hit one variable deeper, where
        # the example disagreed with the code. A test loads the example and
        # asserts the two agree.
        tts_voice_id_en=_resolve(
            file_values, "TTS_VOICE_ID_EN", PROVISIONAL_VOICE_ID_EN
        ),
        tts_voice_id_ar=_resolve(
            file_values, "TTS_VOICE_ID_AR", PROVISIONAL_VOICE_ID_AR
        ),
        tts_voice_id_hi=_resolve(
            file_values, "TTS_VOICE_ID_HI", PROVISIONAL_VOICE_ID_HI
        ),
        guardrail_mode=guardrail_mode,
        prompt_mode=prompt_mode,
        demo_mode=_resolve_bool(file_values, "DEMO_MODE"),
        language=language,
        allow_uncertified_language=_resolve_bool(
            file_values, "ALLOW_UNCERTIFIED_LANGUAGE"
        ),
    )
