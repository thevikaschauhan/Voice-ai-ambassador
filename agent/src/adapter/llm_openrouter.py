"""The OpenRouter LLM, built from the LiveKit OpenAI plugin (ADR-006/016).

Two things happen here, both of them configuration rather than abstraction:

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

Retries on 429 are the provider SDK's, not ours: the observed failure is
Alibaba's shared pool congesting (AGENTS.md project learnings), which is
exactly the case `openai`'s built-in exponential backoff with `Retry-After`
handling covers. The framework then adds an outer retry via `conn_options`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import openai
from livekit.plugins import openai as lk_openai

from .config import Settings

logger = logging.getLogger("ambassador.llm")

UsageCallback = Callable[[dict[str, Any]], None]
StatusCallback = Callable[[int], None]

_SSE_PREFIX = b"data: "


def _extract_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields ADR-016's gate needs out of one OpenRouter usage frame."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    completion_details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        # Absent means the provider reported no reasoning at all, which for
        # this gate is the same as zero. Present-and-nonzero is the alarm.
        "reasoning_tokens": (completion_details.get("reasoning_tokens") or 0),
        "cached_tokens": (prompt_details.get("cached_tokens") or 0),
    }


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
            # The SDK retries these with backoff and the caller never sees
            # them, so without this line an upstream 429 looks like a slow
            # model rather than a congested pool - exactly the misattribution
            # the latency meter must not make.
            logger.warning(
                "openrouter returned %s (retried by the SDK); TTFT for this "
                "turn includes the backoff",
                response.status_code,
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


def build_llm(
    settings: Settings,
    on_usage: UsageCallback,
    on_status: StatusCallback | None = None,
) -> lk_openai.LLM:
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
        # Exponential backoff honouring Retry-After. The observed 429s are
        # upstream pool congestion at Alibaba, not our quota.
        max_retries=4,
    )
    return lk_openai.LLM(
        model=settings.llm_model,
        client=client,
        base_url=settings.llm_base_url,
        # Deterministic by choice: the demo runbook wants three consecutive
        # identical runs, and sampling variance showed up as the escalation
        # tool firing on some runs and not others for the same question.
        temperature=0.0,
        extra_body=extra_body or lk_openai.llm.NOT_GIVEN,  # type: ignore[attr-defined]
    )
