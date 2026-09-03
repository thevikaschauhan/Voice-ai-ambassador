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

from ambassador.inventory import load_inventory
from ambassador.leads import ScoringInputs, load_rubric, score_interest
from ambassador.schemas import LeadAnalysisDraft, LeadSnapshot

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
    except ValueError:
        # The message names the signal and the turn, both of which are ours,
        # but it can also name a project id a model invented - so the event
        # carries the code and the message goes nowhere.
        return await _fail(writer, lead_id, log, stage="score", code="evidence")

    try:
        await writer.repository.put_analysis(
            lead_id,
            status="complete",
            summary=writer.seal(lead_id, "summary", draft.summary.encode("utf-8")),
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

    A model-supplied id that does not resolve in inventory raises, the same way
    an invented turn index does: the lead list is entitled to assume every id
    in its column is a real project.
    """
    inventory = {project.id for project in load_inventory()}
    unknown = [pid for pid in draft.project_ids if pid not in inventory]
    if unknown:
        raise ValueError(
            f"project_ids: {', '.join(unknown)} do not resolve in inventory"
        )
    score = score_interest(
        ScoringInputs(
            draft=draft,
            contact=snapshot.contact,
            started_at=datetime.fromisoformat(snapshot.started_at),
            ended_at=datetime.fromisoformat(snapshot.ended_at),
            buyer_turn_indexes=snapshot.buyer_turn_indexes,
            project_ids_in_inventory=list(draft.project_ids),
        ),
        load_rubric(),
    )
    return score, list(draft.project_ids)


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
