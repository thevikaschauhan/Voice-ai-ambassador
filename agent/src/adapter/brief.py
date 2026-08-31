"""Hook 3: lead-brief extraction, off the latency path.

The third channel of the two-channel turn design (docs/01-, design principle
2): after each agent turn, a separate small-model call extracts a structured
`LeadBrief` from the conversation so far. It runs as a detached asyncio task -
the voice path never awaits it - and the ambassador screen tolerates the
sub-second lag.

Failure handling follows docs/03- exactly:

    brief extraction invalid -> one repair retry -> keep last good brief, log

"Invalid" covers two things: output Pydantic cannot parse, and output that
parses but names a project id the inventory does not contain. The second is
docs/03- validator 3, and silently dropping the bad id is forbidden - it would
hide the exact failure mode this system claims to prevent.

The model is the same Qwen 3.7 Flash slug as the voice path with thinking off
(ADR-016: one model, one key, both channels), called non-streaming through
plain httpx. This is not a second LLM abstraction: the framework's LLM
interface exists to serve a voice session's streaming turn, and this call is
neither streaming nor part of a session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from ambassador.schemas import Language, LeadBrief

logger = logging.getLogger("ambassador.brief")

# Upstream congestion, not our quota: retry the transport, with backoff.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_TRANSPORT_ATTEMPTS = 4

_SYSTEM = """You extract a structured lead brief from a property sales conversation.
Return ONLY a JSON object, no prose and no code fences.

Schema:
{schema}

Rules:
- Use null for anything the buyer has not indicated. Never invent a value.
- shortlist_ids must be ids from this list only: {project_ids}
- budget.currency is the currency the buyer actually named (AED, INR, USD, ...).
- budget.confirmed is true only if the buyer confirmed the amount AND currency.
- stage is the furthest point the conversation has reached.
- language is {language}."""

_REPAIR = (
    "Your previous output was rejected: {error}\n"
    "Return ONLY the corrected JSON object, matching the schema exactly."
)


def _schema_hint() -> str:
    """A compact field list, not the raw JSON Schema. The raw schema carries
    $defs indirection that small models follow poorly; the flat shape is what
    they actually reproduce."""
    return json.dumps(
        {
            "intent": "invest | live | unknown",
            "budget": {
                "amount": "number",
                "currency": "string",
                "confirmed": "boolean",
            },
            "unit_preference": "string or null",
            "timeline": "string or null",
            "buyer_location": "string or null",
            "golden_visa_interest": "boolean or null",
            "hesitations": ["string"],
            "shortlist_ids": ["string"],
            "stage": "opening | discovery | recommendation | objections | booking | escalated",
            "language": "en | ar | hi",
        },
        indent=2,
    )


class BriefExtractor:
    """Owns the last good brief for a session and the extraction task."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        project_ids: list[str],
        language: Language,
        on_event: Callable[..., object],
        thinking_disabled: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._project_ids = project_ids
        self._language = language
        self._on_event = on_event
        self._thinking_disabled = thinking_disabled
        self._client = client
        self._owns_client = client is None
        self._last_good: LeadBrief | None = None
        # Extraction is detached and retries, so turn N can finish after turn
        # N+1 and would otherwise overwrite the newer brief with older data.
        # `_last_good` only ever moves forward.
        self._last_accepted_turn: int | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def last_good(self) -> LeadBrief | None:
        return self._last_good

    @property
    def last_accepted_turn(self) -> int | None:
        return self._last_accepted_turn

    def _accept(self, brief: LeadBrief, turn_index: int) -> bool:
        """Advance the last good brief, unless this result is stale."""
        if (
            self._last_accepted_turn is not None
            and turn_index < self._last_accepted_turn
        ):
            return False
        self._last_good = brief
        self._last_accepted_turn = turn_index
        return True

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=45.0, write=15.0, pool=10.0)
            )
        return self._client

    # -- scheduling -------------------------------------------------------

    def schedule(
        self, transcript: list[dict[str, str]], turn_index: int
    ) -> asyncio.Task[None]:
        """Fire and forget. The caller must not await this - that is the whole
        point of putting the brief on its own channel.

        `turn_index` orders the results. Extraction retries, so a slow turn N
        can land after turn N+1; when it does it is dropped rather than allowed
        to overwrite the newer brief.
        """
        task = asyncio.create_task(
            self._run(transcript, turn_index),
            name=f"brief_extraction_turn_{turn_index}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 10.0) -> None:
        """Only for shutdown and tests; the voice path never calls this."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    # -- extraction -------------------------------------------------------

    def _validate(self, raw_text: str) -> LeadBrief:
        payload = _strip_fences(raw_text)
        brief = LeadBrief.model_validate_json(payload)
        unknown = [i for i in brief.shortlist_ids if i not in self._project_ids]
        if unknown:
            # docs/03- validator 3: an unresolvable id is a guardrail failure,
            # never a silent drop.
            raise ValueError(f"shortlist_ids not in inventory: {', '.join(unknown)}")
        return brief

    async def _run(self, transcript: list[dict[str, str]], turn_index: int) -> None:
        system = _SYSTEM.format(
            schema=_schema_hint(),
            project_ids=", ".join(self._project_ids),
            language=self._language,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "Conversation so far:\n"
                + "\n".join(f"{m['role']}: {m['content']}" for m in transcript),
            },
        ]

        error: str | None = None
        for attempt in ("first", "repair"):
            if attempt == "repair":
                messages = messages + [
                    {"role": "assistant", "content": error or ""},
                    {"role": "user", "content": _REPAIR.format(error=error)},
                ]
            try:
                text, usage = await self._call(messages)
            except Exception as e:  # network, status, timeout
                error = f"{type(e).__name__}: {e}"
                self._on_event(
                    "brief_error", turn=turn_index, attempt=attempt, error=error
                )
                continue

            try:
                brief = self._validate(text)
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                error = str(e)[:400]
                self._on_event(
                    "brief_invalid",
                    turn=turn_index,
                    attempt=attempt,
                    error=error,
                    raw=text[:400],
                )
                continue

            if not self._accept(brief, turn_index):
                self._on_event(
                    "brief_stale_dropped",
                    turn=turn_index,
                    attempt=attempt,
                    last_accepted_turn=self._last_accepted_turn,
                    reason="a later turn's brief is already the last good one",
                )
                return

            self._on_event(
                "brief",
                turn=turn_index,
                attempt=attempt,
                brief=brief.model_dump(),
                reasoning_tokens=usage.get("reasoning_tokens"),
                model=self._model,
            )
            return

        self._on_event(
            "brief_fallback",
            turn=turn_index,
            reason="extraction failed twice",
            error=error,
            kept_last_good=self._last_good is not None,
            brief=None if self._last_good is None else self._last_good.model_dump(),
        )

    async def _post_with_backoff(self, body: dict[str, Any]) -> httpx.Response:
        """Exponential backoff on upstream congestion.

        Transport failure and invalid output are different failures and must
        not share a budget: the voice path gets this from the openai SDK, and
        without it here a single upstream 429 consumes the repair retry that
        exists for malformed JSON. `qwen3.7-flash` 429s under Alibaba
        shared-pool congestion regularly (AGENTS.md project learnings), so this
        is the expected path, not an edge case.
        """
        delay = 0.5
        last: httpx.Response | Exception | None = None
        for attempt in range(_TRANSPORT_ATTEMPTS):
            try:
                response = await self._ensure_client().post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
            except httpx.HTTPError as e:
                last = e
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                last = response
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))

            if attempt < _TRANSPORT_ATTEMPTS - 1:
                self._on_event(
                    "brief_retry",
                    attempt=attempt + 1,
                    delay_s=round(delay, 2),
                    status=getattr(last, "status_code", None),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)

        if isinstance(last, Exception):
            raise last
        assert last is not None
        return last

    async def _call(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 600,
            "response_format": {"type": "json_object"},
        }
        if self._thinking_disabled:
            # Same ADR-016 discipline as the voice path. This call is off the
            # latency path, but thinking here still burns tokens and time for
            # a mechanical extraction.
            body["reasoning"] = {"enabled": False}

        response = await self._post_with_backoff(body)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return text, {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens") or 0,
        }

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _strip_fences(text: str) -> str:
    """Small models wrap JSON in code fences despite being told not to. Peeling
    a fence is formatting, not repair - the Pydantic validation behind it is
    unchanged."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        stripped = stripped.removesuffix("```")
    return stripped.strip()
