"""The Phase 2 store, over asyncpg (ADR-018).

Plain SQL against stock Postgres. No ORM and no Supabase client: the store has
to move to another Postgres host without the domain contracts noticing, and an
ORM would put schema authorship back in Python where ADR-018 took it out.

CONNECTIONS. Supavisor SESSION mode, port 5432 - the IPv4-compatible route for
persistent processes, and the reason `statement_cache_size` is left at its
default here. Transaction mode would need it set to 0 on both `connect()` and
`create_pool()`; that is a different deployment and this module does not
pretend to support both (ryan's supabase-postgres memo).

The pool is small and explicit rather than library-default: at most five per
process, which stays far below the free Nano instance's 200 pooler clients even
with the worker and the admin API both connected. Every acquisition and every
query is bounded, because ADR-018 is explicit that an unavailable database must
never block a call - a write that hangs would do exactly that, and the caller
cannot tell a slow database from a stopped one without a timeout.

This module does NOT encrypt. It stores the envelope it is handed and returns
it unchanged; key handling belongs to the card that owns the keys, and a
repository that silently encrypted would make the boundary impossible to audit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from .migrations import assert_schema_current

# Five is the ADR-018 number. It is a ceiling on this process, not a target.
MAX_POOL_SIZE = 5
# Bounded on purpose (see the module note). Long enough to ride out a pooler
# hiccup, short enough that a stopped database is a failure rather than a hang.
ACQUIRE_TIMEOUT_SECONDS = 5.0
QUERY_TIMEOUT_SECONDS = 10.0

_ENVELOPE_FIELDS = ("algorithm", "key_version", "nonce", "ciphertext")


def _envelope(value: Any) -> dict[str, Any] | None:
    """An `encrypted_envelope` composite as a plain dict, or None."""
    if value is None:
        return None
    return {name: value[name] for name in _ENVELOPE_FIELDS}


def _envelope_tuple(value: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if value is None:
        return None
    return tuple(value[name] for name in _ENVELOPE_FIELDS)


class Repository:
    """Everything the worker and the admin API do to the database."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, check_schema: bool = True) -> "Repository":
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=MAX_POOL_SIZE,
            timeout=ACQUIRE_TIMEOUT_SECONDS,
            command_timeout=QUERY_TIMEOUT_SECONDS,
        )
        assert pool is not None
        repository = cls(pool)
        if check_schema:
            async with pool.acquire() as connection:
                await assert_schema_current(connection)
        return repository

    async def close(self) -> None:
        await self._pool.close()

    # -- leads ------------------------------------------------------------

    async def start_lead(
        self,
        *,
        session_id: str,
        language: str,
        requested_language: str,
        uncertified_fallback: bool,
        inventory_version: str,
        started_at: datetime,
    ) -> Any:
        """Open a lead, or return the one this session already has.

        `session_id` is the idempotency key: a retried persist after a
        half-finished shutdown must not produce a second lead for one call.
        """
        return await self._pool.fetchval(
            """
            INSERT INTO leads (session_id, created_at, language, requested_language,
                               uncertified_fallback, inventory_version)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (session_id) DO UPDATE SET session_id = EXCLUDED.session_id
            RETURNING id
            """,
            session_id,
            started_at,
            language,
            requested_language,
            uncertified_fallback,
            inventory_version,
        )

    async def finish_lead(
        self,
        lead_id: Any,
        *,
        ended_at: datetime,
        call_end_reason: str,
        ended_cleanly: bool,
    ) -> None:
        await self._pool.execute(
            "UPDATE leads SET ended_at = $2, call_end_reason = $3, ended_cleanly = $4"
            " WHERE id = $1",
            lead_id,
            ended_at,
            call_end_reason,
            ended_cleanly,
        )

    async def set_ambassador_name(self, lead_id: Any, name: str) -> None:
        """Which ambassador answered. Part of the lead, because the client
        chose three names and an admin needs to know who the buyer believes
        they spoke to."""
        await self._pool.execute(
            "UPDATE leads SET ambassador_name = $2 WHERE id = $1", lead_id, name
        )

    async def put_brief(self, lead_id: Any, brief: dict[str, Any] | None) -> None:
        await self._pool.execute(
            "UPDATE leads SET brief = $2 WHERE id = $1",
            lead_id,
            _envelope_tuple(brief),
        )

    async def put_contact(
        self,
        lead_id: Any,
        *,
        status: str,
        asked_turn_index: int | None,
        source_turn_index: int | None,
        name: dict[str, Any] | None,
        phone: dict[str, Any] | None,
        email: dict[str, Any] | None,
        phone_fingerprint: str | None,
        email_fingerprint: str | None,
        contact_permission: bool,
        confirmed: bool,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE leads SET
                contact_status = $2,
                contact_asked_turn_index = $3,
                contact_source_turn_index = $4,
                contact_name = $5,
                contact_phone = $6,
                contact_email = $7,
                contact_phone_fingerprint = $8,
                contact_email_fingerprint = $9,
                contact_permission = $10,
                contact_confirmed = $11
            WHERE id = $1
            """,
            lead_id,
            status,
            asked_turn_index,
            source_turn_index,
            _envelope_tuple(name),
            _envelope_tuple(phone),
            _envelope_tuple(email),
            phone_fingerprint,
            email_fingerprint,
            contact_permission,
            confirmed,
        )

    async def put_analysis(
        self,
        lead_id: Any,
        *,
        status: str,
        summary: dict[str, Any] | None,
        score_total: int | None,
        score_version: str | None,
        breakdown: list[dict[str, Any]] | None,
    ) -> None:
        import json

        await self._pool.execute(
            """
            UPDATE leads SET analysis_status = $2, summary = $3,
                             score_total = $4, score_version = $5,
                             score_breakdown = $6::jsonb
            WHERE id = $1
            """,
            lead_id,
            status,
            _envelope_tuple(summary),
            score_total,
            score_version,
            json.dumps(breakdown) if breakdown is not None else None,
        )

    async def get_lead(self, lead_id: Any) -> dict[str, Any]:
        row = await self._pool.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
        if row is None:
            raise LookupError(f"no lead {lead_id}")
        lead = dict(row)
        for field in (
            "brief",
            "summary",
            "contact_name",
            "contact_phone",
            "contact_email",
        ):
            lead[field] = _envelope(lead[field])
        return lead

    async def add_turn(
        self,
        lead_id: Any,
        *,
        turn_index: int,
        timestamp: datetime,
        audit_incomplete: bool,
        payload: dict[str, Any],
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO lead_turns (lead_id, turn_index, timestamp,
                                    audit_incomplete, payload)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (lead_id, turn_index) DO UPDATE
                SET payload = EXCLUDED.payload,
                    audit_incomplete = EXCLUDED.audit_incomplete
            """,
            lead_id,
            turn_index,
            timestamp,
            audit_incomplete,
            _envelope_tuple(payload),
        )

    async def get_turns(self, lead_id: Any) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM lead_turns WHERE lead_id = $1 ORDER BY turn_index",
            lead_id,
        )
        turns = []
        for row in rows:
            turn = dict(row)
            turn["payload"] = _envelope(turn["payload"])
            turns.append(turn)
        return turns

    # -- decisions --------------------------------------------------------

    async def record_decision(
        self,
        lead_id: Any,
        *,
        new_status: str,
        reason_code: str,
        note: dict[str, Any] | None,
        actor_kind: str,
        actor_id: Any,
        expected_lead_revision: int,
    ) -> Any:
        """Append the decision and move the lead, in ONE transaction.

        docs/02- requires both or neither: a lead whose status disagrees with
        its own decision history cannot be reconciled after the fact. The
        revision check makes a concurrent second admin fail rather than
        silently overwrite the first.
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                lead = await connection.fetchrow(
                    "SELECT status, revision FROM leads WHERE id = $1 FOR UPDATE",
                    lead_id,
                )
                if lead is None:
                    raise LookupError(f"no lead {lead_id}")
                if lead["revision"] != expected_lead_revision:
                    raise ConcurrentDecision(
                        f"lead {lead_id} moved to revision {lead['revision']} while "
                        f"a decision expecting {expected_lead_revision} was in flight"
                    )
                sequence = await connection.fetchval(
                    "SELECT coalesce(max(sequence), 0) + 1 FROM admin_decisions"
                    " WHERE lead_id = $1",
                    lead_id,
                )
                decision_id = await connection.fetchval(
                    """
                    INSERT INTO admin_decisions (
                        lead_id, sequence, previous_status, new_status, reason_code,
                        note, actor_kind, actor_id, expected_lead_revision)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    lead_id,
                    sequence,
                    lead["status"],
                    new_status,
                    reason_code,
                    _envelope_tuple(note),
                    actor_kind,
                    actor_id,
                    expected_lead_revision,
                )
                await connection.execute(
                    "UPDATE leads SET status = $2, revision = revision + 1"
                    " WHERE id = $1",
                    lead_id,
                    new_status,
                )
                return decision_id

    async def get_decisions(self, lead_id: Any) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM admin_decisions WHERE lead_id = $1 ORDER BY sequence",
            lead_id,
        )
        decisions = []
        for row in rows:
            decision = dict(row)
            decision["note"] = _envelope(decision["note"])
            decisions.append(decision)
        return decisions

    # -- knowledge --------------------------------------------------------

    async def add_document(
        self,
        *,
        revision: int,
        title: str,
        source_type: str,
        original_filename: str | None,
        mime_type: str,
        source_bytes: int,
        source_sha256: str,
        extracted_text: str,
    ) -> Any:
        return await self._pool.fetchval(
            """
            INSERT INTO knowledge_documents (
                revision, title, source_type, original_filename, mime_type,
                source_bytes, source_sha256, extracted_text, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'draft')
            RETURNING id
            """,
            revision,
            title,
            source_type,
            original_filename,
            mime_type,
            source_bytes,
            source_sha256,
            extracted_text,
        )

    async def add_chunk(
        self,
        document_id: Any,
        *,
        document_revision: int,
        ordinal: int,
        heading: str | None,
        body: str,
        content_sha256: str,
    ) -> Any:
        """A chunk arrives CLOSED. Scope is a review, never an ingestion
        decision (docs/02-), so this takes no scope argument at all."""
        return await self._pool.fetchval(
            """
            INSERT INTO knowledge_chunks (
                document_id, document_revision, ordinal, heading, body,
                content_sha256)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            document_id,
            document_revision,
            ordinal,
            heading,
            body,
            content_sha256,
        )

    async def add_figure(
        self,
        document_id: Any,
        *,
        document_revision: int,
        chunk_id: Any,
        value: str,
        kind: str,
        currency: str | None = None,
        unit: str | None = None,
        surface: str,
        source_sentence: str,
        page: int | None = None,
    ) -> Any:
        """Parsing creates figures and never approves them (docs/02-)."""
        return await self._pool.fetchval(
            """
            INSERT INTO knowledge_figures (
                document_id, document_revision, chunk_id, value, kind, currency,
                unit, surface, source_sentence, page)
            VALUES ($1, $2, $3, $4::numeric, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            document_id,
            document_revision,
            chunk_id,
            value,
            kind,
            currency,
            unit,
            surface,
            source_sentence,
            page,
        )

    async def get_chunk(self, chunk_id: Any) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            "SELECT * FROM knowledge_chunks WHERE id = $1", chunk_id
        )
        if row is None:
            raise LookupError(f"no chunk {chunk_id}")
        return dict(row)

    async def get_figure(self, figure_id: Any) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            "SELECT * FROM knowledge_figures WHERE id = $1", figure_id
        )
        if row is None:
            raise LookupError(f"no figure {figure_id}")
        return dict(row)

    async def review_chunk(
        self,
        chunk_id: Any,
        *,
        action: str,
        project_id: str | None,
        actor_kind: str,
        actor_id: Any = None,
    ) -> Any:
        """Append the review and re-project the chunk's scope, together.

        `scope_review_id` on the chunk is a PROJECTION of this history, so the
        two must move in one transaction or the projection can outlive a review
        that was never recorded.
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                review_id = await connection.fetchval(
                    """
                    INSERT INTO knowledge_chunk_reviews (
                        chunk_id, action, project_id, actor_kind, actor_id)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                    """,
                    chunk_id,
                    action,
                    project_id,
                    actor_kind,
                    actor_id,
                )
                prompt_eligible = action in ("general_knowledge", "project_knowledge")
                await connection.execute(
                    """
                    UPDATE knowledge_chunks
                    SET retrieval_scope = $2,
                        project_id = $3,
                        scope_review_id = $4,
                        prompt_body = CASE WHEN $5 THEN body ELSE NULL END
                    WHERE id = $1
                    """,
                    chunk_id,
                    action,
                    project_id,
                    review_id,
                    prompt_eligible,
                )
                return review_id

    async def review_figure(
        self,
        figure_id: Any,
        *,
        action: str,
        actor_kind: str,
        actor_id: Any = None,
    ) -> Any:
        """Approve or revoke. `active_approval_id` is the projection; a
        revocation clears it rather than deleting the history."""
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                review_id = await connection.fetchval(
                    """
                    INSERT INTO knowledge_figure_reviews (
                        figure_id, action, actor_kind, actor_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    figure_id,
                    action,
                    actor_kind,
                    actor_id,
                )
                await connection.execute(
                    "UPDATE knowledge_figures SET active_approval_id = $2 WHERE id = $1",
                    figure_id,
                    review_id if action == "approved" else None,
                )
                return review_id

    async def record_knowledge_use(
        self,
        lead_id: Any,
        *,
        turn_index: int,
        query_fingerprint: str,
        chunk_refs: list[dict[str, Any]],
        figure_review_ids: list[Any],
        withheld_figure_match: bool,
        elapsed_ms: int,
    ) -> None:
        """Freeze what this turn was allowed to see (docs/02-)."""
        import json

        await self._pool.execute(
            """
            INSERT INTO knowledge_use (
                lead_id, turn_index, query_fingerprint, chunk_refs,
                figure_review_ids, withheld_figure_match, elapsed_ms)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            ON CONFLICT (lead_id, turn_index) DO NOTHING
            """,
            lead_id,
            turn_index,
            query_fingerprint,
            json.dumps(chunk_refs),
            figure_review_ids,
            withheld_figure_match,
            elapsed_ms,
        )

    async def get_knowledge_use(
        self, lead_id: Any, *, turn_index: int
    ) -> dict[str, Any]:
        import json

        row = await self._pool.fetchrow(
            "SELECT * FROM knowledge_use WHERE lead_id = $1 AND turn_index = $2",
            lead_id,
            turn_index,
        )
        if row is None:
            raise LookupError(f"no knowledge use for turn {turn_index}")
        use = dict(row)
        use["chunk_refs"] = json.loads(use["chunk_refs"])
        return use

    # -- audit ------------------------------------------------------------

    async def add_audit_event(
        self, lead_id: Any, *, event: str, detail: dict[str, Any] | None = None
    ) -> None:
        import json

        await self._pool.execute(
            "INSERT INTO audit_events (lead_id, event, detail)"
            " VALUES ($1, $2, $3::jsonb)",
            lead_id,
            event,
            json.dumps(detail or {}),
        )

    async def get_audit_events(self, lead_id: Any) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM audit_events WHERE lead_id = $1 ORDER BY id", lead_id
        )
        return [dict(row) for row in rows]


class ConcurrentDecision(RuntimeError):
    """A second admin decided while this one was in flight."""
