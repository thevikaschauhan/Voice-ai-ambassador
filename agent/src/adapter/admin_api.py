"""The private admin API (ADR-021, docs/10-).

A small FastAPI app that owns the domain and lets the repository own the
database. It is private: no public Railway domain, reached only from inside the
project network by the web service's fixed proxy routes, and every route except
`/health` requires the shared bearer.

## The bearer, and why it is compared the way it is

`ADMIN_API_TOKEN` is a shared secret that a public web tier proxies with, so a
plain `==` on it is a timing oracle for its length and prefix.
`secrets.compare_digest` is the whole reason the comparison is not inline.

Unset is CLOSED, and an unset token returns the same 401 as a wrong one. That
symmetry is deliberate: telling the two apart would let an unauthenticated
caller learn whether the service is configured, which is deployment state and
none of their business.

## `/health` and `/ready` answer different questions

`/health` is process liveness and nothing else. It takes no bearer, touches no
database, and stays 200 while Postgres is paused - because Railway restarts a
service whose health check fails, and ADR-018's free tier pauses the database
after about a week of inactivity. A health check that went red on a paused
database would turn a recoverable pause into a restart loop.

`/ready` is the opposite question and is bearer-protected, because naming the
database and the schema is deployment state. It answers 503 rather than raising
when Postgres will not answer: a pause is a normal condition here, not an
error.

## The keep-alive probe

ADR-018's mitigation for the inactivity pause: one cheap query a day. It emits
`database_health_probe` whether or not the database answered, because a probe
that silently stopped running looks exactly like one that is working. It never
raises - a probe that takes the service down over the condition it exists to
observe is worse than no probe - and it carries no exception string, since a
driver error can quote a DSN and this event is on the durable stream.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .events import EventLog
from .repository import ConcurrentDecision, Repository

_TOKEN_ENV: Final = "ADMIN_API_TOKEN"
_DSN_ENV: Final = "DATABASE_URL"

# ADR-018: the free tier pauses after roughly a week of inactivity, so one
# query a day is enough to keep it awake with no measurable cost.
PROBE_INTERVAL_SECONDS: Final = 24 * 60 * 60

# One body for every refusal, so a caller cannot tell an unset token from a
# wrong one.
_UNAUTHORISED: Final[dict[str, str]] = {"detail": "unauthorised"}


def _authorised(header: str | None) -> bool:
    """Whether this Authorization header carries the configured bearer.

    Length-guarded before the constant-time compare because `compare_digest`
    on strings of different lengths returns early; the guard makes that
    explicit rather than relying on it.
    """
    configured = os.environ.get(_TOKEN_ENV) or ""
    if not configured or not header:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme != "Bearer" or not presented:
        return False
    if len(presented) != len(configured):
        return False
    return secrets.compare_digest(presented, configured)


async def require_bearer(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """The guard on every route except `/health`.

    A dependency rather than middleware so that FastAPI resolves it BEFORE the
    handler body runs, and therefore before anything is looked up: a route that
    read the lead and then checked the bearer would already have done the work
    an unauthenticated caller asked for.
    """
    if not _authorised(authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHORISED["detail"],
            headers={"WWW-Authenticate": "Bearer"},
        )


async def run_database_probe(repository: Any, log: EventLog) -> bool:
    """One keep-alive round trip, reported either way. Never raises."""
    try:
        await repository.ping()
    except Exception:
        # Deliberately no exception text: a driver error can quote a DSN, and
        # docs/10- keeps exception strings off the durable stream. The boolean
        # is the whole signal, and the service log carries the detail.
        log.emit("database_health_probe", reachable=False)
        return False
    log.emit("database_health_probe", reachable=True)
    return True


async def _probe_forever(app: FastAPI) -> None:
    log: EventLog = app.state.log
    while True:
        # Sleep FIRST. `Repository.connect` already verified the database and
        # the schema at start-up, so a probe on boot proves nothing new, adds a
        # round trip to every deploy, and - the reason it was caught - makes
        # the database look touched by requests that never touched it.
        await asyncio.sleep(PROBE_INTERVAL_SECONDS)
        repository = getattr(app.state, "repository", None)
        if repository is not None:
            await run_database_probe(repository, log)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect on start, if a DSN is set, and keep the probe running.

    A missing DSN does not stop the app: `/health` still answers, which is what
    keeps a misconfigured deployment diagnosable instead of restart-looping
    before it can be looked at. `/ready` reports the truth in that state.
    """
    if not hasattr(app.state, "log"):
        app.state.log = EventLog(session_id="admin-api")
    if not hasattr(app.state, "repository"):
        dsn = os.environ.get(_DSN_ENV)
        app.state.repository = await Repository.connect(dsn) if dsn else None
    probe = asyncio.create_task(_probe_forever(app))
    try:
        yield
    finally:
        probe.cancel()


app = FastAPI(
    title="Binghatti admin API",
    # No public docs: the surface is private and its shapes are documented in
    # docs/10- for the one client that calls it.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def repository_of(app_: FastAPI) -> Any:
    repository = getattr(app_.state, "repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database is unavailable",
        )
    return repository


# --- liveness and readiness ----------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness only. No bearer, no database, no counts."""
    return {"status": "ok"}


@app.get("/ready", dependencies=[Depends(require_bearer)])
async def ready(response: Response) -> dict[str, str]:
    repository = getattr(app.state, "repository", None)
    if repository is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}
    try:
        await repository.ping()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}
    return {"status": "ready"}


# --- leads ---------------------------------------------------------------

LeadStatusFilter = Literal["unreviewed", "qualified", "rejected"]


@app.get("/v1/leads", dependencies=[Depends(require_bearer)])
async def list_leads(
    lead_status: Annotated[LeadStatusFilter | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    """Operational fields only; the projection is enforced in the repository.

    docs/10- keeps buyer words and contact values on the detail page, and the
    list query names its columns rather than selecting everything - so a list
    response cannot leak a transcript it was never handed.
    """
    return await repository_of(app).list_leads(
        status=lead_status, limit=limit, offset=offset
    )


@app.get("/v1/leads/{lead_id}", dependencies=[Depends(require_bearer)])
async def get_lead(lead_id: str) -> dict[str, Any]:
    repository = repository_of(app)
    try:
        lead = await repository.get_lead(lead_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lead") from None
    lead["turns"] = await repository.get_turns(lead_id)
    lead["decisions"] = await repository.get_decisions(lead_id)
    return lead


@app.post("/v1/leads/{lead_id}/analysis-retry", dependencies=[Depends(require_bearer)])
async def retry_analysis(lead_id: str) -> dict[str, str]:
    """docs/10-'s bounded retry for a failed analysis.

    Only a `failed` lead is eligible, which the repository enforces: retrying a
    complete analysis would discard a score an admin may already have acted on.
    """
    try:
        await repository_of(app).reset_analysis(lead_id)
    except LookupError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "lead is not in a failed analysis state"
        ) from None
    return {"analysis_status": "pending"}


class DecisionRequest(BaseModel):
    new_status: Literal["qualified", "rejected"]
    reason_code: Literal[
        "ready",
        "follow_up",
        "not_interested",
        "invalid_contact",
        "outside_scope",
        "duplicate",
        "other",
    ]
    note: str | None = None
    expected_lead_revision: int = Field(ge=0)


@app.post("/v1/leads/{lead_id}/decisions", dependencies=[Depends(require_bearer)])
async def append_decision(lead_id: str, body: DecisionRequest) -> dict[str, Any]:
    """Append-only, with the optimistic revision check.

    A moved revision is 409 rather than a silent overwrite: two admins reading
    the same lead and deciding differently is the case the counter exists for,
    and the second one has to see the first's decision before repeating theirs.
    """
    try:
        decision_id = await repository_of(app).record_decision(
            lead_id,
            new_status=body.new_status,
            reason_code=body.reason_code,
            note={"note": body.note} if body.note is not None else None,
            actor_kind="admin",
            actor_id=None,
            expected_lead_revision=body.expected_lead_revision,
        )
    except ConcurrentDecision:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "lead revision has moved"
        ) from None
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such lead") from None
    return {"decision_id": str(decision_id)}


@app.get("/v1/leads/{lead_id}/decisions", dependencies=[Depends(require_bearer)])
async def list_decisions(lead_id: str) -> list[dict[str, Any]]:
    return await repository_of(app).get_decisions(lead_id)


# --- knowledge -----------------------------------------------------------


@app.get("/v1/knowledge/documents", dependencies=[Depends(require_bearer)])
async def list_documents() -> list[dict[str, Any]]:
    return await repository_of(app).list_documents()


@app.get(
    "/v1/knowledge/documents/{document_id}", dependencies=[Depends(require_bearer)]
)
async def get_document(document_id: str) -> dict[str, Any]:
    """The parse result, its chunks and every extracted figure.

    Figures come back approved and unapproved together, because the review list
    IS the unapproved ones - and docs/10- is explicit that approving a value
    without its sentence and page is not review.
    """
    repository = repository_of(app)
    try:
        document = await repository.get_document(document_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document") from None
    revision = document["revision"]
    document["chunks"] = await repository.get_chunks(document_id, revision=revision)
    document["figures"] = await repository.get_figures(document_id, revision=revision)
    return document


class ChunkReviewRequest(BaseModel):
    action: Literal[
        "general_knowledge", "project_knowledge", "inventory_governed", "admin_only"
    ]
    project_id: str | None = None


@app.post(
    "/v1/knowledge/chunks/{chunk_id}/reviews", dependencies=[Depends(require_bearer)]
)
async def review_chunk(chunk_id: str, body: ChunkReviewRequest) -> dict[str, Any]:
    """Append a scope review. Project scope requires an inventory id.

    The binding rule is enforced in the repository and in `ambassador.knowledge`
    rather than restated here: a route that decided scope for itself would be a
    second implementation of the closure, and the two would disagree.
    """
    try:
        review_id = await repository_of(app).review_chunk(
            chunk_id,
            action=body.action,
            project_id=body.project_id,
            actor_kind="admin",
            actor_id=None,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such chunk") from None
    except ValueError:
        # A fixed message rather than the repository's own text. The rule being
        # broken is known and worth naming; the exception string is a path for
        # anything the driver or a future validator decides to include, and
        # this response crosses a service boundary.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "project_knowledge requires a project_id that resolves to inventory",
        ) from None
    return {"review_id": str(review_id)}


class FigureReviewRequest(BaseModel):
    action: Literal["approved", "revoked"]


@app.post(
    "/v1/knowledge/figures/{figure_id}/reviews", dependencies=[Depends(require_bearer)]
)
async def review_figure(figure_id: str, body: FigureReviewRequest) -> dict[str, Any]:
    """Approve or revoke ONE occurrence.

    Approval never changes a chunk's scope: an `inventory_governed` chunk stays
    closed however many of its figures are approved (ADR-019).
    """
    try:
        review_id = await repository_of(app).review_figure(
            figure_id,
            action=body.action,
            actor_kind="admin",
            actor_id=None,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such figure") from None
    return {"review_id": str(review_id)}
