"""Custom STT node: Qwen3-ASR through OpenRouter's transcription endpoint.

ADR-015 (amended) chose Qwen3-ASR on OpenRouter, and named the consequence:
LiveKit has no OpenRouter STT plugin, and OpenRouter's transcription endpoint
takes base64 JSON rather than OpenAI's multipart form, so the OpenAI STT plugin
cannot be pointed at it. This module is the "tens of lines of transport glue"
that ADR-015 sanctions - it lives in the adapter, never in the core, and it
implements the framework's own `STT` interface rather than inventing one.

Non-streaming by declaration (`streaming=False`). That is not a limitation
worked around, it is the turn design: transcription happens once, on endpoint
(turn flow steps 1-2). Because the capability is declared honestly, the
framework's default `stt_node` wraps this in `stt.StreamAdapter` with the
session VAD automatically - VAD segments the audio, this transcribes each
segment. No custom node override is needed to get the StreamAdapter pattern.

Not live-tested: OpenRouter rejects audio requests while the account balance is
under $0.50 (402), per AGENTS.md project learnings. The request shape is
covered by a unit test against a mocked transport, and `STT_ENABLED` defaults
to false so the agent runs in text mode without it.
"""

from __future__ import annotations

import base64
import io
import logging
import wave
from dataclasses import dataclass

import httpx
import numpy as np
from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils.audio import AudioBuffer

logger = logging.getLogger("ambassador.stt")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
TRANSCRIPTIONS_PATH = "/audio/transcriptions"
# Measured 2026-08-29, one 4-second utterance, six warm calls each, same
# transcript from every shape: WAV 16k = 174KB base64, p50 1035ms; WAV 8k =
# 84KB, p50 732ms; MP3 32kbps = 21KB, p50 578ms. The upload was most of the
# latency, not the model, and latency is near-flat in utterance length
# (~520-820ms from 1.5s to 9.7s of audio), so the payload is the lever. MP3 it
# is. No new dependency: PyAV ships with livekit-agents and carries libmp3lame.
# 32kbps mono is well above what speech recognition needs and the transcript is
# byte-identical to the uncompressed original.
AUDIO_FORMAT = "mp3"
MP3_BITRATE = 32_000


@dataclass(frozen=True)
class _Options:
    model: str
    language: str
    base_url: str
    api_key: str


def frames_to_wav_bytes(buffer: AudioBuffer) -> bytes:
    """One VAD-segmented utterance as a self-contained WAV container.

    The fallback when MP3 encoding is unavailable, and the reference the
    encoder is tested against. OpenRouter needs a format it can decode
    standalone, and the framework hands us raw PCM frames, so the header has to
    be written here.
    """
    frame = rtc.combine_audio_frames(buffer)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(frame.num_channels)
        wav.setsampwidth(2)  # rtc.AudioFrame is 16-bit PCM
        wav.setframerate(frame.sample_rate)
        wav.writeframes(frame.data.tobytes())
    return out.getvalue()


def frames_to_mp3_bytes(buffer: AudioBuffer) -> bytes:
    """The same utterance as MP3, roughly an eighth of the bytes.

    Raises RuntimeError if PyAV or libmp3lame is unavailable, so the caller can
    fall back to WAV rather than dropping the turn: a slower transcription is
    recoverable, a missing one is a silent turn.
    """
    import av  # PyAV, already present via livekit-agents

    frame = rtc.combine_audio_frames(buffer)
    layout = "mono" if frame.num_channels == 1 else "stereo"
    out = io.BytesIO()
    try:
        with av.open(out, mode="w", format="mp3") as container:
            stream = container.add_stream("libmp3lame", rate=frame.sample_rate)
            stream.bit_rate = MP3_BITRATE
            source = av.AudioFrame.from_ndarray(
                np.frombuffer(frame.data, dtype=np.int16).reshape(1, -1),
                format="s16",
                layout=layout,
            )
            source.sample_rate = frame.sample_rate
            for packet in stream.encode(source):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
    except Exception as exc:  # codec missing, unusual sample rate, PyAV change
        raise RuntimeError(f"mp3 encoding unavailable: {exc}") from exc
    return out.getvalue()


def encode_utterance(buffer: AudioBuffer) -> tuple[bytes, str]:
    """Encoded audio plus the format label OpenRouter should be told.

    MP3 when it works, WAV when it does not. The format label travels with the
    bytes so the two can never disagree on the wire.
    """
    try:
        return frames_to_mp3_bytes(buffer), AUDIO_FORMAT
    except RuntimeError as exc:
        logger.warning("falling back to wav for this utterance: %s", exc)
        return frames_to_wav_bytes(buffer), "wav"


def build_request_body(
    *, model: str, audio_bytes: bytes, language: str, audio_format: str = AUDIO_FORMAT
) -> dict[str, object]:
    """The exact JSON OpenRouter's transcription endpoint expects.

    Deliberately a pure function so the wire shape is unit-testable without a
    network, an event loop, or an audio device.
    """
    return {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
        "language": language,
    }


class OpenRouterSTT(stt.STT):
    """Per-utterance transcription. Declared non-streaming so the framework
    wraps it in a VAD StreamAdapter (ADR-015, turn flow steps 1-2)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "qwen/qwen3-asr-1.7b",
        language: str = "en",
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        if not api_key:
            raise ValueError(
                "OpenRouter API key is required for STT; set OPENROUTER_API_KEY"
            )
        self._opts = _Options(
            model=model, language=language, base_url=base_url, api_key=api_key
        )
        self._client = client
        self._owns_client = client is None

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "openrouter"

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
            )
        return self._client

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = (
            language if isinstance(language, str) and language else self._opts.language
        )
        audio_bytes, audio_format = encode_utterance(buffer)
        body = build_request_body(
            model=self._opts.model,
            audio_bytes=audio_bytes,
            language=lang,
            audio_format=audio_format,
        )
        url = self._opts.base_url.rstrip("/") + TRANSCRIPTIONS_PATH

        try:
            response = await self._ensure_client().post(
                url,
                headers={"Authorization": f"Bearer {self._opts.api_key}"},
                json=body,
                timeout=conn_options.timeout,
            )
        except httpx.TimeoutException as e:
            raise APITimeoutError() from e
        except httpx.HTTPError as e:
            raise APIConnectionError(str(e)) from e

        if response.status_code != 200:
            # Body may name the account state (a 402 under $0.50 balance is the
            # known one) but never carries our key, so it is safe to surface.
            raise APIStatusError(
                response.text[:300],
                status_code=response.status_code,
                request_id=response.headers.get("x-request-id"),
            )

        data = response.json()
        text = (data.get("text") or "").strip()
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=data.get("id") or utils.shortuuid(),
            alternatives=[stt.SpeechData(language=lang, text=text)],
        )

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
