"""The OpenRouter LLM, built from the LiveKit OpenAI plugin (ADR-006/016).

Three things happen here, all of them configuration rather than abstraction:

1. **Thinking is disabled.** ADR-016's named trap is that reasoning is ON by
   default for this model, and thinking tokens are generated *before* the reply
   streams. The plugin forwards `extra_body` straight into the request body, so
   OpenRouter's unified `reasoning` parameter reaches Alibaba without a custom
   LLM class. No subclass, no wrapper - the plugin already exposes the seam.

2. **Reasoning tokens are observed.** The framework normalises provider usage
   into `CompletionUsage`, which has no reasoning-token field: the number
   arrives from OpenRouter and is discarded before any application code sees
   it. ADR-016 requires checking it ("check the response usage for reasoning
   tokens, not just the latency"), so a pass-through httpx transport reads the
   `usage` frame out of the SSE body as it streams past. It copies; it never
   consumes, buffers or alters the response. This is observability underneath
   the plugin, not a second path around it - every request still goes through
   the plugin exactly as configured.

3. **Retries are bounded, once.** The observed failure is Alibaba's shared pool
   congesting (AGENTS.md project learnings), and both the `openai` SDK and the
   framework will retry it. Stacked, that is up to twenty attempts and, because
   the SDK honours `Retry-After` up to two minutes, a turn that hangs long past
   the point a caller would hang up. So: one retry in the SDK, one in the
   framework via an explicit `APIConnectOptions`, and an upper bound clamped
   onto any `Retry-After` the provider sends. Worst-case added delay is a
   couple of seconds, not a couple of minutes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Final, NamedTuple, TypedDict

import httpx
import openai
from livekit.agents import APIConnectOptions
from livekit.plugins import openai as lk_openai

from .config import Settings

logger = logging.getLogger("ambassador.llm")


class UsageFrame(TypedDict):
    """One OpenRouter usage frame, reduced to what ADR-016's gate reads.

    `reasoning_tokens` and `cached_tokens` are never None: absent means the
    provider reported none, which for the gate is zero. Prompt and completion
    counts can genuinely be missing, and a missing count must not read as zero.
    """

    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int
    cached_tokens: int


UsageCallback = Callable[[UsageFrame], None]
StatusCallback = Callable[[int], None]

_SSE_PREFIX = b"data: "

# The SDK sleeps for whatever Retry-After says, up to MAX_RETRY_AFTER_DELAY
# (120s in the installed openai). On a live call a two-minute pause is
# indistinguishable from a dropped line, so the header is clamped before the
# SDK ever reads it.
_RETRY_AFTER_CEILING_S: Final = 1.0

# One retry each side. The framework's first retry interval is fixed at 0.1s by
# `APIConnectOptions._interval_for_retry(0)`, so `retry_interval` only governs
# a second one that cannot happen at max_retry=1; it is set low anyway so the
# bound does not depend on that detail holding.
_SDK_MAX_RETRIES: Final = 1
_FRAMEWORK_MAX_RETRY: Final = 1
_FRAMEWORK_RETRY_INTERVAL_S: Final = 0.3

# Passed explicitly at every `chat()` call. Without it the framework falls back
# to DEFAULT_API_CONNECT_OPTIONS (max_retry=3, retry_interval=2.0), which
# multiplies against the SDK's own retries instead of bounding them.
CONN_OPTIONS: Final = APIConnectOptions(
    max_retry=_FRAMEWORK_MAX_RETRY,
    retry_interval=_FRAMEWORK_RETRY_INTERVAL_S,
)


def _extract_usage(payload: dict[str, Any]) -> UsageFrame | None:
    """Pull the fields ADR-016's gate needs out of one OpenRouter usage frame."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    completion_details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return UsageFrame(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        # Absent means the provider reported no reasoning at all, which for
        # this gate is the same as zero. Present-and-nonzero is the alarm.
        reasoning_tokens=(completion_details.get("reasoning_tokens") or 0),
        cached_tokens=(prompt_details.get("cached_tokens") or 0),
    )


def clamp_retry_after(headers: httpx.Headers) -> float | None:
    """Cap the Retry-After the SDK will honour. Returns the original value when
    it was clamped, so the caller can log what the provider actually asked for.

    `openai._base_client` reads `retry-after-ms` first and `retry-after`
    second, so writing a bounded `retry-after-ms` and dropping `retry-after` is
    what the SDK ends up sleeping on.
    """
    raw_ms = headers.get("retry-after-ms")
    raw_s = headers.get("retry-after")
    if raw_ms is None and raw_s is None:
        return None

    requested: float | None = None
    if raw_ms is not None:
        try:
            requested = float(raw_ms) / 1000
        except ValueError:
            requested = None
    if requested is None and raw_s is not None:
        try:
            requested = float(raw_s)
        except ValueError:
            # An HTTP-date. Not worth parsing to decide; clamp it regardless.
            requested = float("inf")
    if requested is None or requested <= _RETRY_AFTER_CEILING_S:
        return None

    if "retry-after" in headers:
        del headers["retry-after"]
    headers["retry-after-ms"] = str(int(_RETRY_AFTER_CEILING_S * 1000))
    return requested


class _UsageTappedStream(httpx.AsyncByteStream):
    """Forwards the response body unchanged while copying out usage frames."""

    def __init__(self, inner: Any, on_usage: UsageCallback) -> None:
        self._inner = inner
        self._on_usage = on_usage
        self._buffer = b""

    def _scan(self, chunk: bytes) -> None:
        self._buffer += chunk
        # Keep only the tail after the last newline; usage arrives in the final
        # frames, so the buffer never grows past one partial line.
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            line = line.strip()
            if not line.startswith(_SSE_PREFIX):
                continue
            body = line[len(_SSE_PREFIX) :]
            if body == b"[DONE]":
                continue
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            usage = _extract_usage(payload)
            if usage is not None:
                try:
                    self._on_usage(usage)
                except Exception:  # never let telemetry break the voice path
                    logger.exception("usage callback failed")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._scan(chunk)
            yield chunk

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()


class UsageTappingTransport(httpx.AsyncBaseTransport):
    """httpx transport that tees SSE usage frames to a callback."""

    def __init__(
        self,
        on_usage: UsageCallback,
        inner: httpx.AsyncBaseTransport | None = None,
        on_status: StatusCallback | None = None,
    ):
        self._on_usage = on_usage
        self._on_status = on_status
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if response.status_code >= 400:
            requested = clamp_retry_after(response.headers)
            # The SDK retries these with backoff and the caller never sees
            # them, so without this line an upstream 429 looks like a slow
            # model rather than a congested pool - exactly the misattribution
            # the latency meter must not make.
            logger.warning(
                "openrouter returned %s (retried by the SDK); TTFT for this "
                "turn includes the backoff%s",
                response.status_code,
                ""
                if requested is None
                else f"; Retry-After {requested:.0f}s clamped to "
                f"{_RETRY_AFTER_CEILING_S:.1f}s",
            )
            if self._on_status is not None:
                try:
                    self._on_status(response.status_code)
                except Exception:
                    logger.exception("status callback failed")
            return response
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_UsageTappedStream(response.stream, self._on_usage),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class BuiltLLM(NamedTuple):
    """The configured plugin LLM plus the httpx client underneath it.

    The plugin sets `_owns_client = False` for a client it was handed, so
    `lk_openai.LLM.aclose()` deliberately leaves this one open. Whoever built
    it has to close it, which is why it is returned rather than hidden.
    """

    llm: lk_openai.LLM
    http_client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.http_client.aclose()


def build_llm(
    settings: Settings,
    on_usage: UsageCallback,
    on_status: StatusCallback | None = None,
) -> BuiltLLM:
    """The voice-path LLM. Pure plugin configuration (ADR-006)."""
    extra_body: dict[str, Any] = {}
    if settings.thinking_disabled:
        # OpenRouter's unified reasoning parameter, forwarded to Alibaba's
        # enable_thinking. Verified live: reasoning_tokens comes back 0.
        extra_body["reasoning"] = {"enabled": False}

    http_client = httpx.AsyncClient(
        transport=UsageTappingTransport(on_usage, on_status=on_status),
        timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0),
        follow_redirects=True,
    )
    client = openai.AsyncClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.llm_base_url,
        http_client=http_client,
        # One retry, and the transport above has already clamped any
        # Retry-After it would sleep on. The framework adds exactly one more
        # (see CONN_OPTIONS); anything beyond that is latency the caller reads
        # as a dead line.
        max_retries=_SDK_MAX_RETRIES,
    )
    llm = lk_openai.LLM(
        model=settings.llm_model,
        client=client,
        base_url=settings.llm_base_url,
        # Deterministic by choice: the demo runbook wants three consecutive
        # identical runs, and sampling variance showed up as the escalation
        # tool firing on some runs and not others for the same question.
        temperature=0.0,
        extra_body=extra_body or lk_openai.llm.NOT_GIVEN,  # type: ignore[attr-defined]
    )
    return BuiltLLM(llm=llm, http_client=http_client)
