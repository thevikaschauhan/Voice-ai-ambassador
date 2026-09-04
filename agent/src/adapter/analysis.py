"""Steps 5-6 of docs/10- Lead finalisation: summarise, then score in code.

The division of labour is the point of this module. The model reads the
transcript and returns a `LeadAnalysisDraft`: a summary and which rubric
signals it believes it saw, with the turns it saw them in. It returns NO score
and no points, because a number a model chose cannot be explained to an admin
and cannot be recomputed - and ADR-020 is explicit that the score is
deterministic and the decision stays human.

Everything the model says about evidence is then checked against the record
that was actually SAVED. An index it invented, or a project id that does not
resolve in inventory, fails the analysis rather than being dropped: a signal
quietly scoring on half its evidence shows an admin the points and a shorter
list of turns, with nothing saying why.

A FAILED ANALYSIS IS NOT A MISSING LEAD. The snapshot is already durable by
the time this runs, so every failure path here ends at
`analysis_status=failed`, which the admin API can retry, and never at a call
nobody has a record of.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Final

from ambassador.inventory import load_inventory, resolve_project_id
from ambassador.leads import ScoringInputs, load_rubric, score_interest
from ambassador.schemas import LeadAnalysisDraft, LeadSnapshot

from . import field_paths
from .events import EventLog
from .persist import LeadWriter

# `ask(prompt, repair=False) -> str`. A callable rather than an httpx client so
# the tests can exercise what happens to the LEAD; mocking a client would only
# prove that a client can be mocked.
Ask = Callable[..., Awaitable[str]]

# One retry, as docs/10- specifies. A model that cannot produce the shape twice
# will not produce it on a third attempt, and this runs while a Railway deploy
# waits to drain.
_ATTEMPTS: Final = ("first", "repair")

_STAGES: Final = ("ask", "validate", "score", "write")


async def finalise_analysis(
    *,
    snapshot: LeadSnapshot,
    lead_id: Any,
    writer: LeadWriter,
    ask: Ask,
    log: EventLog,
) -> bool:
    """Summarise and score one persisted lead. True when it completed.

    Never raises. Finalisation runs inside the shutdown path, so a failure here
    must not stop the job ending any more than a failed persist does.
    """
    draft: LeadAnalysisDraft | None = None
    stage = "ask"
    for attempt in _ATTEMPTS:
        try:
            answer = await ask(_prompt(snapshot), repair=(attempt == "repair"))
        except Exception:
            stage = "ask"
            code = "upstream"
            log.emit("analysis_attempt_failed", attempt=attempt, stage=stage, code=code)
            continue
        try:
            draft = _validate(answer)
            break
        except Exception:
            # No detail: an invalid response is the model's text, and the
            # validation error quotes it.
            stage = "validate"
            log.emit(
                "analysis_attempt_failed", attempt=attempt, stage=stage, code="invalid"
            )

    if draft is None:
        return await _fail(writer, lead_id, log, stage=stage, code="no_valid_draft")

    try:
        score, project_ids = _score(snapshot, draft)
    except UnknownProject:
        # Its own code, because its own operator action: an id that resolves to
        # nothing means the model named something we do not sell, or the
        # instruction did not say what we do. `evidence` would send whoever
        # reads the event to the transcript instead.
        return await _fail(writer, lead_id, log, stage="score", code="unknown_project")
    except ValueError:
        # The message names the signal and the turn, both of which are ours,
        # but it can also name a project id a model invented - so the event
        # carries the code and the message goes nowhere.
        return await _fail(writer, lead_id, log, stage="score", code="evidence")

    try:
        await writer.repository.put_analysis(
            lead_id,
            status="complete",
            summary=writer.seal(
                lead_id, field_paths.summary(), draft.summary.encode("utf-8")
            ),
            score_total=score.total,
            score_version=score.score_version,
            breakdown=[json.loads(item.model_dump_json()) for item in score.breakdown],
            project_ids=project_ids,
        )
    except Exception as exc:
        return await _fail(writer, lead_id, log, stage="write", code=_write_code(exc))

    log.emit("analysis_complete", total=score.total, version=score.score_version)
    return True


def _prompt(snapshot: LeadSnapshot) -> str:
    """The transcript the model reads. Buyer text, by necessity - this is the
    one place it is sent anywhere, and it goes to the same server-side model the
    brief already uses rather than to a new vendor."""
    lines = [
        f"turn {turn.turn_index} buyer: {turn.buyer_utterance}\n"
        f"turn {turn.turn_index} ambassador: {' '.join(turn.generated_sentences)}"
        for turn in snapshot.turns
    ]
    return "\n".join(lines)


def _validate(answer: str) -> LeadAnalysisDraft:
    payload = answer.strip()
    if payload.startswith("```"):
        payload = payload.strip("`")
        payload = payload.split("\n", 1)[-1] if "\n" in payload else payload
    return LeadAnalysisDraft.model_validate_json(payload)


def _score(snapshot: LeadSnapshot, draft: LeadAnalysisDraft):
    """The rubric's total, and the project ids that survived validation.

    A model that answers with NAMES is answering the question it was asked -
    the instruction lists ids now, but a name is still resolved against
    inventory rather than rejected, because "Binghatti Skyrise" and
    `binghatti-skyrise` are the same claim about the same call.

    A value that resolves to nothing still raises, the same way an invented
    turn index does: the lead list is entitled to assume every id in its column
    is a real project. It raises `UnknownProject` so the failure can be filed
    under its own code - an operator who sees `evidence` goes to the transcript,
    and this one is about the inventory and the prompt.
    """
    projects = load_inventory()
    resolved: list[str] = []
    unknown: list[str] = []
    for value in draft.project_ids:
        project_id = resolve_project_id(value, projects)
        if project_id is None:
            unknown.append(value)
        elif project_id not in resolved:
            # Resolution makes duplicates likelier than they were: a model can
            # send the id and the name for one project. The Projects column
            # should not list it twice, and the rubric scores the signal rather
            # than the count, so collapsing them changes no score.
            resolved.append(project_id)
    if unknown:
        raise UnknownProject(
            f"project_ids: {', '.join(unknown)} do not resolve in inventory"
        )
    # The rubric checks the same thing from core, against the draft it is
    # handed - so it has to be handed the RESOLVED draft, or it re-raises the
    # failure this function just resolved. Its check stays worth having: it is
    # what catches an adapter that forgets to resolve.
    score = score_interest(
        ScoringInputs(
            draft=draft.model_copy(update={"project_ids": resolved}),
            contact=snapshot.contact,
            started_at=datetime.fromisoformat(snapshot.started_at),
            ended_at=datetime.fromisoformat(snapshot.ended_at),
            buyer_turn_indexes=snapshot.buyer_turn_indexes,
            project_ids_in_inventory=resolved,
        ),
        load_rubric(),
    )
    return score, resolved


def _write_code(exc: BaseException) -> str:
    from .persist import _failure_code

    return _failure_code(exc)


async def _fail(
    writer: LeadWriter,
    lead_id: Any,
    log: EventLog,
    *,
    stage: str,
    code: str,
) -> bool:
    """Mark the lead failed and say why in enum terms.

    The mark itself can fail - the database may be the reason we are here - so
    it is guarded too. A lead stuck at `pending` is still a lead with a
    transcript, which is the outcome ADR-020 asks for.
    """
    log.emit("analysis_failed", stage=stage, code=code)
    try:
        await writer.repository.put_analysis(
            lead_id,
            status="failed",
            summary=None,
            score_total=None,
            score_version=None,
            breakdown=None,
        )
    except Exception:
        log.emit("analysis_status_unwritten", stage="write", code="unavailable")
    return False


def analysis_body(settings: Any, prompt: str, *, repair: bool) -> dict[str, Any]:
    """The request body, shaped like `brief.py:_call`.

    A pure function, because the discipline in this dict IS the fix and it
    should be reviewable without a vendor call. The first version sent `model`
    and `messages` and nothing else, which left THINKING ON: measured, 16.3s
    and 1982 reasoning tokens on qwen3.7-flash where this shape returns in
    1.9-2.4s. Neither the per-request timeout nor the analysis cap could be met
    on any call.

    Every field earns its place:

      stream=False        one complete JSON object, not deltas.
      temperature=0.0     a summary that varies between runs cannot be checked
                          against a rubric.
      max_tokens          a bound on cost and on time, for output whose useful
                          size is known.
      response_format     json_object, so a model that would have written prose
                          is refused by the endpoint rather than by our
                          validator one round trip later.
      reasoning disabled  under the SAME `thinking_disabled` setting the brief
                          extractor honours (ADR-016), never a hardcoded false:
                          an operator who turned thinking on has said
                          something, and this is not the place to overrule them
                          silently.
    """
    body: dict[str, Any] = {
        "model": settings.analysis_model,
        "messages": [
            {"role": "system", "content": analysis_instruction(repair=repair)},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.0,
        # A summary plus six small signal objects. The brief extractor's number
        # for the same shape of output.
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }
    if settings.thinking_disabled:
        body["reasoning"] = {"enabled": False}
    return body


def analysis_ask(settings: Any) -> Ask | None:
    """The session-analysis model call, or None when there is no key.

    Reuses the OpenRouter endpoint and the brief model the brief extractor
    already uses (`adapter/brief.py`): one vendor, one key, one place the
    boundary is documented. A second provider for one summary would be a new
    key to leak and a new failure mode on the shutdown path. It now reuses the
    request SHAPE too, which is what this first missed.

    Bounded per request as well as by the caller's overall budget, because the
    caller's timeout protects the shutdown and this one protects the retry: a
    first attempt that eats the whole budget leaves nothing for the repair.
    """
    if not settings.openrouter_api_key:
        return None

    import httpx

    async def ask(prompt: str, *, repair: bool = False) -> str:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json=analysis_body(settings, prompt, repair=repair),
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    return ask


# Sized against god's measurements rather than picked: the brief-shaped request
# returns in 1.9-2.4s on qwen3.7-flash and gemini-2.5-flash, and 1.5s typically
# on gemini-2.5-flash-lite. 2.5s clears the measured common case. The 3.9s
# outlier gemini-2.5-flash-lite produced once will time out, and that is the
# trade: a failed analysis is a state an operator can retry, while a timeout
# generous enough for every outlier lets two attempts outlive the audit seal.
#
# Two attempts have to fit `agent.ANALYSIS_BUDGET_SECONDS`, because docs/10-
# allows one repair and a repair the budget cannot afford is not a repair.
REQUEST_TIMEOUT_SECONDS: Final = 2.5


class UnknownProject(ValueError):
    """A project id or name that resolves to nothing in inventory.

    A `ValueError` still, so every existing handler keeps catching it; a
    distinct type so the one place that cares can tell it apart.
    """


def analysis_instruction(*, repair: bool) -> str:
    """The system message, with the inventory ids IN it.

    Built rather than constant because the ids come from `load_inventory()`,
    the same source `brief.py:_SYSTEM` interpolates. Asking a model for
    "project ids" without saying what they are is a question it cannot answer,
    and on the human's 08:32Z call it answered with the names the conversation
    used - correctly - and lost the whole analysis at the scoring step.
    """
    catalogue = "; ".join(f"{p.id} ({p.name})" for p in load_inventory())
    parts = [
        _ANALYSIS_INSTRUCTION,
        "project_ids must be ids from this list only, never names: "
        f"{catalogue}. Return an empty list [] when no project was named.",
    ]
    if repair:
        parts.insert(0, _REPAIR_PREFIX)
    return "\n".join(parts)


_ANALYSIS_INSTRUCTION: Final = (
    "You are summarising one sales call for an internal admin view. Return ONLY "
    "a JSON object with these keys: summary (2-3 sentences), budget_stated, "
    "project_named, timeline_stated, viewing_or_human_requested (each an object "
    '{"observed": bool, "turn_indexes": [int]}), project_ids (a list of project '
    "ids mentioned) and question_turn_indexes (buyer turns that asked a "
    "question). Cite only turn numbers that appear in the transcript. Do NOT "
    "return a score, a total or any points: those are computed elsewhere."
)

_REPAIR_PREFIX: Final = (
    "Your previous answer was not valid JSON of the required shape. Return ONLY "
    "the JSON object described, with no prose and no code fence."
)
