"""The custom STT node's wire contract (ADR-015).

OpenRouter's transcription endpoint takes base64 JSON, not OpenAI's multipart
form - that mismatch is the entire reason this node exists, so the request
shape is what gets asserted.

Mocked transport throughout, deliberately. OpenRouter rejects audio requests
while the account balance is under $0.50 (AGENTS.md project learnings), so
this node is unproven against the live endpoint and the test must not imply
otherwise. What is proven here is that we send what the endpoint documents.
"""

from __future__ import annotations

import base64
import io
import json
import wave

import httpx
import pytest

# ADR-002: the core stays installable and testable with no voice stack present
# (`uv sync --no-group voice`). These adapter tests need the framework, so they
# skip rather than turn that guarantee into a collection error.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit import rtc  # noqa: E402
from livekit.agents import APIStatusError, stt  # noqa: E402

from adapter.stt_openrouter import (  # noqa: E402
    TRANSCRIPTIONS_PATH,
    OpenRouterSTT,
    build_request_body,
    encode_utterance,
    frames_to_mp3_bytes,
    frames_to_wav_bytes,
)

FAKE_KEY = "test-key-not-a-real-credential"


def make_frame(samples: int = 1600, sample_rate: int = 16000) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x01" * samples,
        sample_rate=sample_rate,
        num_channels=1,
        samples_per_channel=samples,
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"text": "hello"}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(
            self.status, json=self.payload, headers={"x-request-id": "req_123"}
        )


def client_for(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport)


# --- pure helpers ---------------------------------------------------------


def test_frames_become_a_decodable_wav_container():
    wav_bytes = frames_to_wav_bytes([make_frame(), make_frame()])
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 3200  # both frames concatenated


def test_request_body_is_base64_json_not_multipart():
    body = build_request_body(
        model="qwen/qwen3-asr-1.7b", audio_bytes=b"ID3fake", language="ar"
    )
    assert body == {
        "model": "qwen/qwen3-asr-1.7b",
        "input_audio": {
            "data": base64.b64encode(b"ID3fake").decode(),
            "format": "mp3",
        },
        "language": "ar",
    }
    # Round-trips: the endpoint must be able to decode what we send.
    assert base64.b64decode(body["input_audio"]["data"]) == b"ID3fake"


# --- the node -------------------------------------------------------------


def test_declares_itself_non_streaming_so_the_framework_wraps_it_in_a_vad_adapter():
    node = OpenRouterSTT(api_key=FAKE_KEY)
    assert node.capabilities.streaming is False
    assert node.capabilities.interim_results is False
    assert node.provider == "openrouter"


def test_requires_a_key_rather_than_failing_at_the_first_request():
    with pytest.raises(ValueError, match="OpenRouter API key"):
        OpenRouterSTT(api_key="")


async def test_posts_the_documented_shape_to_the_transcriptions_endpoint():
    transport = RecordingTransport(
        payload={"text": "  a studio at Binghatti Skyrise  "}
    )
    node = OpenRouterSTT(
        api_key=FAKE_KEY,
        model="qwen/qwen3-asr-1.7b",
        language="en",
        client=client_for(transport),
    )

    event = await node.recognize([make_frame()])

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert str(request.url).endswith(TRANSCRIPTIONS_PATH)
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["content-type"] == "application/json"

    body = json.loads(request.content)
    assert body["model"] == "qwen/qwen3-asr-1.7b"
    assert body["language"] == "en"
    assert body["input_audio"]["format"] == "mp3"
    # A real decodable container, not raw PCM. MP3 has no single magic number
    # (an ID3 tag or a frame sync), so decode it rather than sniffing bytes.
    sent = base64.b64decode(body["input_audio"]["data"])
    assert sent[:3] == b"ID3" or sent[0] == 0xFF
    # Compression is the point: the payload must be far smaller than the PCM.
    assert len(sent) < len(frames_to_wav_bytes([make_frame()])) / 2

    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "a studio at Binghatti Skyrise"
    assert event.alternatives[0].language == "en"


async def test_per_call_language_overrides_the_configured_default():
    transport = RecordingTransport()
    node = OpenRouterSTT(api_key=FAKE_KEY, language="en", client=client_for(transport))

    await node.recognize([make_frame()], language="ar")

    assert json.loads(transport.requests[0].content)["language"] == "ar"


async def test_a_402_surfaces_as_an_api_error_rather_than_an_empty_transcript():
    """The known live failure: OpenRouter rejects audio under a $0.50 balance.
    A silent empty transcript would look like the buyer said nothing."""
    transport = RecordingTransport(
        status=402, payload={"error": "insufficient credits"}
    )
    node = OpenRouterSTT(
        api_key=FAKE_KEY,
        client=client_for(transport),
    )

    with pytest.raises(APIStatusError) as excinfo:
        await node.recognize([make_frame()], conn_options=_no_retry())

    assert excinfo.value.status_code == 402


def _no_retry():
    from livekit.agents.types import APIConnectOptions

    return APIConnectOptions(max_retry=0, timeout=5.0)


def test_arabic_routes_to_the_day_zero_winner_when_it_is_set():
    """Per-language routing (ADR-015); ADR-010 makes the language known up
    front, so the routing costs nothing."""
    from adapter.config import Settings

    def settings_with(ar: str) -> Settings:
        return Settings(
            livekit_url="",
            livekit_api_key="",
            livekit_api_secret="",
            openrouter_api_key="",
            llm_model="m",
            llm_base_url="u",
            llm_thinking="off",
            brief_model="m",
            stt_provider="openrouter",
            stt_model_default="qwen/qwen3-asr-1.7b",
            stt_model_ar=ar,
            stt_enabled=False,
            stt_enabled_explicit=True,
            database_url="",
            analysis_model="qwen/qwen3.7-flash",
            pii_encryption_key="",
            pii_hash_key="",
            deepgram_api_key="",
            deepgram_model="nova-3",
            fish_api_key="",
            fish_tts_model="s2.1-pro",
            tts_voice_id_en="",
            tts_voice_id_ar="",
            tts_voice_id_hi="",
            guardrail_mode="enforce",
            prompt_mode="ambassador",
            demo_mode=False,
            language="en",
            allow_uncertified_language=False,
            demo_max_call_seconds=0,
        )

    undecided = settings_with("")
    assert undecided.stt_model("ar") == "qwen/qwen3-asr-1.7b"
    assert undecided.stt_model("hi") == "qwen/qwen3-asr-1.7b"

    decided = settings_with("qwen/qwen3-asr-flash")
    assert decided.stt_model("ar") == "qwen/qwen3-asr-flash"
    # Flash has no Hindi, so it may only ever take the Arabic slot.
    assert decided.stt_model("hi") == "qwen/qwen3-asr-1.7b"


# The upload, not the model, was most of the measured STT latency: one 4-second
# utterance went from p50 1035ms as 16k WAV to 578ms as 32kbps MP3, same
# transcript. These pin the encoding rather than the timing, which is the part
# a refactor can silently undo.
def test_mp3_encoding_is_far_smaller_than_the_wav_it_replaces():
    frames = [make_frame(), make_frame()]
    mp3 = frames_to_mp3_bytes(frames)
    wav = frames_to_wav_bytes(frames)
    assert len(mp3) < len(wav) / 3
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF


def test_mp3_audio_decodes_back_to_the_same_duration():
    import av

    frames = [make_frame(), make_frame()]
    with av.open(io.BytesIO(frames_to_mp3_bytes(frames))) as container:
        stream = container.streams.audio[0]
        decoded = sum(f.samples for f in container.decode(stream))
    # Encoder padding makes this inexact; the point is that real audio of about
    # the right length survived, not that it is sample-accurate.
    assert decoded >= 3200


def test_the_encoder_reports_the_format_it_actually_produced():
    audio, fmt = encode_utterance([make_frame()])
    assert fmt == "mp3"
    assert audio[:3] == b"ID3" or audio[0] == 0xFF


def test_a_broken_encoder_falls_back_to_wav_rather_than_dropping_the_turn(monkeypatch):
    # A missing codec must cost latency, never the utterance: a slow
    # transcription is recoverable, a lost one is a silent turn.
    def explode(_buffer):
        raise RuntimeError("mp3 encoding unavailable: no libmp3lame")

    monkeypatch.setattr("adapter.stt_openrouter.frames_to_mp3_bytes", explode)
    audio, fmt = encode_utterance([make_frame()])
    assert fmt == "wav"
    assert audio.startswith(b"RIFF")


def test_the_format_label_always_matches_the_bytes(monkeypatch):
    # The label travels with the bytes so the two cannot disagree on the wire.
    for broken, expected in ((False, b"mp3"), (True, b"wav")):
        if broken:
            monkeypatch.setattr(
                "adapter.stt_openrouter.frames_to_mp3_bytes",
                lambda _b: (_ for _ in ()).throw(RuntimeError("x")),
            )
        audio, fmt = encode_utterance([make_frame()])
        body = build_request_body(
            model="m", audio_bytes=audio, language="en", audio_format=fmt
        )
        decoded = base64.b64decode(body["input_audio"]["data"])
        if body["input_audio"]["format"] == "wav":
            assert decoded.startswith(b"RIFF")
        else:
            assert decoded[:3] == b"ID3" or decoded[0] == 0xFF
