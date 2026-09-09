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
from collections.abc import Callable, Sequence
from typing import Any

import asyncpg

from ambassador.schemas import KnowledgePublicationRequest

from .migrations import assert_schema_current
from .session_mode import assert_session_mode

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
        # Before the pool, not after a failed handshake: an unreachable
        # transaction-mode host would otherwise fail as a connection error and
        # hide the reason. No second startup line here - the runtime already
        # logs host:port once from the lead store.
        assert_session_mode(dsn)
        # These two defaults are what makes "Application startup complete" in the
        # admin API's log mean something (ryan, from the live start): min_size=1
        # opens a connection eagerly, so the line proves the pool CONNECTED, and
        # check_schema=True below proves the migration check ran. Change either
        # to lazy and that log line silently stops carrying either claim, while
        # still being printed.
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
        project_ids: list[str] | None = None,
    ) -> None:
        import json

        await self._pool.execute(
            """
            UPDATE leads SET analysis_status = $2, summary = $3,
                             score_total = $4, score_version = $5,
                             score_breakdown = $6::jsonb,
                             project_ids = coalesce($7, project_ids)
            WHERE id = $1
            """,
            lead_id,
            status,
            _envelope_tuple(summary),
            score_total,
            score_version,
            json.dumps(breakdown) if breakdown is not None else None,
            project_ids,
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

    # -- reads the admin API needs (task-p2-admin-api) --------------------
    #
    # Added here rather than in the route handlers because ADR-021 gives the
    # repository the database and the API the domain, and SQL in a handler
    # would be a second place the schema is known. Read-only: no schema change
    # and no new column.

    async def ping(self) -> None:
        """Cheapest possible round trip, for `/ready` and the keep-alive probe.

        `SELECT 1` rather than a schema check: readiness asks whether Postgres
        is answering, and the schema is verified once at connect
        (`assert_schema_current`). A pause under ADR-018's free tier shows up
        here as a raised connection error, which is the caller's to interpret.
        """
        await self._pool.fetchval("SELECT 1")

    async def list_leads(
        self,
        *,
        status: str | None = None,
        language: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """The lead LIST projection, and the column list is the security control.

        docs/10-: "The lead list shows only operational fields... Buyer words
        and contact values appear on the detail page only." So this deliberately
        does not `SELECT *` the way `get_lead` does - it names its columns, and
        `brief`, `summary`, `contact_name`, `contact_phone` and `contact_email`
        are absent from that list. A list page cannot leak a transcript it was
        never handed, and `SELECT *` here would put every one of those on the
        wire the day the route forgot to filter.

        `contact_status` IS included: whether contact was captured is
        operational, and the value is not. Filters are AND-ed and each is
        optional, so a caller that passes none gets the whole list and one that
        passes several gets the intersection - which is what dropdowns on a
        page produce.

        `project_ids` is in the projection and in the clear. They are OUR
        inventory identifiers, already public in the catalogue, and nothing
        about a buyer is recoverable from one - which is exactly why the same
        shortlist stays sealed as `LeadBrief.shortlist_ids`: there it sits
        inside a model-inferred record ABOUT a person, beside their budget and
        their hesitations. The fix for the list was never to open the brief.

        The project filter is CONTAINMENT (`@>`), not `= ANY`. They are the
        same filter over the same rows and only `@>` can use the GIN index
        from migration 0003; on 60k rows `= ANY` measured as a sequential scan
        of 59,880 rows at 13.4ms against a bitmap index scan at 7.6ms. The
        natural-reading form is the one that cannot use the index, and nobody
        reads a WHERE clause and thinks about the plan.
        """
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, created_at, ended_at, call_end_reason,
                   ended_cleanly, language, requested_language,
                   uncertified_fallback, analysis_status, score_total,
                   score_version, status, revision, contact_status,
                   project_ids
            FROM leads
            WHERE ($1::text IS NULL OR status = $1)
              AND ($2::text IS NULL OR language = $2)
              AND ($3::text IS NULL OR project_ids @> ARRAY[$3]::text[])
            ORDER BY created_at DESC
            LIMIT $4 OFFSET $5
            """,
            status,
            language,
            project_id,
            limit,
            offset,
        )
        return [dict(row) for row in rows]

    async def reset_analysis(self, lead_id: Any) -> None:
        """Put a failed analysis back to pending, for docs/10-'s bounded retry.

        Deliberately narrow: it moves the status and clears the previous
        failure's outputs, and it does NOT touch the turns or the brief. The
        durable call survives a retry that fails again, which is the whole
        reason the snapshot is written before analysis is attempted.
        """
        result = await self._pool.execute(
            """
            UPDATE leads
            SET analysis_status = 'pending', summary = NULL,
                score_total = NULL, score_version = NULL, score_breakdown = NULL
            WHERE id = $1 AND analysis_status = 'failed'
            """,
            lead_id,
        )
        if result.endswith(" 0"):
            raise LookupError(f"no failed lead {lead_id}")

    # -- knowledge reads --------------------------------------------------

    async def list_documents(self) -> list[dict[str, Any]]:
        """Latest revision of each document, without the extracted text.

        The text is excluded for the same reason the lead list excludes the
        transcript: it is the commercial content, a list does not need it, and
        a response that never carries it cannot leak it.

        `figures_pending` counts the figures awaiting approval on the revision
        the row describes. It is here rather than on the detail route because
        the overview's "Needs attention" panel needs it for every document at
        once, and asking per document is an N+1 against the API.

        Three things about the count are contract rather than detail:

        - it is keyed on `(document_id, document_revision)`, not on the
          document alone. Figures belong to a revision, so a superseded
          revision's unapproved figures are not awaiting anything.
        - `active_approval_id IS NULL` is the question, not the absence of a
          review row. `knowledge_figure_reviews` is append-only, so an
          approved-then-revoked figure still has its approval on record and
          only the projection says it no longer carries one - it is pending
          again, and counting review rows would miss that.
        - the aggregate is grouped ONCE and joined, rather than run as a
          correlated count per row. `knowledge_figures` is indexed on
          `chunk_id` only, so a per-row count is a fresh scan of the table
          for every revision of every document.
        """
        rows = await self._pool.fetch(
            """
            WITH pending_figures AS (
                SELECT document_id, document_revision, count(*) AS pending
                FROM knowledge_figures
                WHERE active_approval_id IS NULL
                GROUP BY document_id, document_revision
            )
            SELECT DISTINCT ON (id)
                   id, revision, title, source_type, original_filename,
                   mime_type, source_bytes, status, parse_error_code,
                   created_at, updated_at, published_at,
                   coalesce(pending_figures.pending, 0)::int AS figures_pending
            FROM knowledge_documents
            LEFT JOIN pending_figures
                   ON pending_figures.document_id = knowledge_documents.id
                  AND pending_figures.document_revision
                      = knowledge_documents.revision
            ORDER BY id, revision DESC
            """
        )
        return [dict(row) for row in rows]

    async def get_document(
        self, document_id: Any, *, revision: int | None = None
    ) -> dict[str, Any]:
        """One document revision, text included. The latest when unspecified."""
        if revision is None:
            row = await self._pool.fetchrow(
                """
                SELECT * FROM knowledge_documents
                WHERE id = $1 ORDER BY revision DESC LIMIT 1
                """,
                document_id,
            )
        else:
            row = await self._pool.fetchrow(
                """
                SELECT * FROM knowledge_documents
                WHERE id = $1 AND revision = $2
                """,
                document_id,
                revision,
            )
        if row is None:
            raise LookupError(f"no document {document_id}")
        return dict(row)

    async def get_chunks(
        self, document_id: Any, *, revision: int
    ) -> list[dict[str, Any]]:
        # Columns named rather than `SELECT *`: the table carries a
        # `search_vector` tsvector, which is an index artefact rather than
        # domain data and has no JSON form to hand an API client.
        rows = await self._pool.fetch(
            """
            SELECT id, document_id, document_revision, ordinal, heading, body,
                   retrieval_scope, project_id, scope_review_id, conflict_code,
                   prompt_body, page_start, page_end, content_sha256
            FROM knowledge_chunks
            WHERE document_id = $1 AND document_revision = $2
            ORDER BY ordinal
            """,
            document_id,
            revision,
        )
        return [dict(row) for row in rows]

    async def get_figures(
        self, document_id: Any, *, revision: int
    ) -> list[dict[str, Any]]:
        """Every extracted occurrence, approved or not.

        The admin review list needs the unapproved ones - that is what it is
        for - so this does not filter on `active_approval_id`.
        """
        rows = await self._pool.fetch(
            """
            SELECT * FROM knowledge_figures
            WHERE document_id = $1 AND document_revision = $2
            ORDER BY chunk_id, id
            """,
            document_id,
            revision,
        )
        return [dict(row) for row in rows]

    async def search_chunks(
        self, tokens: Sequence[str], *, project_ids: Sequence[str], limit: int = 4
    ) -> list[dict[str, Any]]:
        """Full-text search over what a call is allowed to be told.

        Takes TOKENS, not an utterance. `plainto_tsquery` joins every token
        with AND and the `simple` configuration strips no stopwords, so
        handing it a spoken sentence produced a query requiring `how` and
        `much` and `the` to appear in a brochure paragraph - and the seam
        matched nothing on real speech while every keyword-shaped test
        passed. `adapter.retrieval.content_tokens` drops the stopwords; this
        ORs what survives.

        `simple` stays on the INDEX side (ADR-019: stemming Arabic or Hindi
        with English rules is worse than not stemming), and the query is
        built from lexemes Postgres itself produced, so nothing user-typed is
        ever concatenated into SQL.

        Two filters are the security boundary, and only two. A document must
        be published, and `prompt_body IS NOT NULL` is the schema's own
        `only_reviewed_scopes_reach_the_prompt` constraint read from the
        other side. The caller re-checks scope in code, because a query is a
        filter and the gate is a rule.

        The minimum-match rule is what keeps OR from meaning "everything":
        two distinct query lexemes must hit, so one ordinary noun in common
        does not qualify a chunk to answer any question. A one-word question
        still needs its one word.

        Project chunks bound to a project this turn is about sort first;
        general knowledge stays eligible on every turn.
        """
        if isinstance(tokens, str):
            raise TypeError("search_chunks takes tokens, not an utterance")
        terms = [token for token in tokens if token]
        if not terms:
            return []
        rows = await self._pool.fetch(
            """
            WITH lexeme AS (
                SELECT DISTINCT plainto_tsquery('simple', t) AS q
                FROM unnest($1::text[]) AS t
                WHERE plainto_tsquery('simple', t) <> ''::tsquery
            ),
            query AS (
                SELECT string_agg(q::text, ' | ')::tsquery AS any_of,
                       count(*) AS terms
                FROM lexeme
            )
            SELECT c.id, c.document_id, c.document_revision, c.heading,
                   c.prompt_body, c.retrieval_scope, c.project_id,
                   c.conflict_code
            FROM knowledge_chunks c
            JOIN knowledge_documents d
              ON d.id = c.document_id AND d.revision = c.document_revision
            CROSS JOIN query
            WHERE query.any_of IS NOT NULL
              AND d.status = 'published'
              AND c.prompt_body IS NOT NULL
              AND c.retrieval_scope IN ('general_knowledge', 'project_knowledge')
              AND (
                    c.retrieval_scope = 'general_knowledge'
                    OR c.project_id = ANY($2::text[])
                  )
              AND c.search_vector @@ query.any_of
              AND (
                    SELECT count(*) FROM lexeme
                    WHERE c.search_vector @@ lexeme.q
                  ) >= least(2, query.terms)
            ORDER BY (c.retrieval_scope = 'project_knowledge'
                      AND c.project_id = ANY($2::text[])) DESC,
                     ts_rank_cd(c.search_vector, query.any_of) DESC,
                     c.id
            LIMIT $3
            """,
            terms,
            list(project_ids),
            limit,
        )
        return [dict(row) for row in rows]

    async def figures_for_chunks(
        self, chunk_ids: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """Every occurrence on the retrieved chunks, approved or not.

        The unapproved ones are needed, not filtered away: the caller has to
        replace their surfaces in the excerpt before the model sees them, and
        it cannot replace what it was not told about.

        `approved` is derived from `active_approval_id`, which is the
        projection of the append-only review history - so a revocation reads
        as False here without a second query.
        """
        rows = await self._pool.fetch(
            """
            SELECT id, chunk_id, value, kind, currency, unit, surface,
                   source_sentence, (active_approval_id IS NOT NULL) AS approved
            FROM knowledge_figures
            WHERE chunk_id = ANY($1::uuid[])
            ORDER BY chunk_id, id
            """,
            list(chunk_ids),
        )
        return [dict(row) for row in rows]

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
        seal_note: Callable[[int], dict[str, Any] | None] | None = None,
        actor_kind: str,
        actor_id: Any,
        expected_lead_revision: int,
    ) -> Any:
        """Append the decision and move the lead, in ONE transaction.

        docs/02- requires both or neither: a lead whose status disagrees with
        its own decision history cannot be reconciled after the fact. The
        revision check makes a concurrent second admin fail rather than
        silently overwrite the first.

        `seal_note` is a CALLBACK rather than an envelope because the note's
        AAD names the sequence, and the sequence is allocated in here, under
        the row lock. A caller cannot know it beforehand: guessing would work
        for the first decision on a lead and fail for every one after. So the
        caller hands over the sealing, and gets told the number.

        It stays a callback rather than a `Sealer` argument so this layer
        never learns what a key is. The repository moves envelopes; it does
        not make them.
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                lead = await connection.fetchrow(
                    "SELECT status, revision FROM leads WHERE id = $1 FOR UPDATE",
                    lead_id,
                )
                if lead is None:
                    raise NoSuchLead(f"no lead {lead_id}")
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
                    # Sealed here, with the sequence the transaction just
                    # allocated, because that number is the note's AAD.
                    _envelope_tuple(seal_note(sequence) if seal_note else None),
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

    async def get_document_review(self, document_id: str) -> dict[str, Any]:
        from .document_review import read_review

        async with self._pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read"):
                return await read_review(connection, document_id)

    async def publish_document(
        self, document_id: str, request: KnowledgePublicationRequest
    ) -> dict[str, Any]:
        """Lock, validate, append every decision and publish, or change nothing."""
        import json

        from ambassador.document_review import publication_scopes
        from ambassador.inventory import load_inventory
        from ambassador.knowledge import chunk_text, load_limits
        from ambassador.schemas import KnowledgeReviewChunk
        from collections import Counter
        from decimal import Decimal
        from .ingestion import figures_in
        from .document_review import read_review

        payload = request.model_dump(mode="json")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                # Every review writer takes this parent lock before child writes.
                await connection.fetch(
                    "SELECT revision FROM knowledge_documents WHERE id = $1 ORDER BY revision FOR UPDATE",
                    document_id,
                )
                previous = await connection.fetchrow(
                    "SELECT * FROM knowledge_document_publications WHERE request_id = $1",
                    request.request_id,
                )
                if previous is not None:
                    if (
                        str(previous["document_id"]) != document_id
                        or json.loads(previous["request"]) != payload
                    ):
                        raise ValueError("This publication request was already used.")
                    return json.loads(previous["result"])
                document = await read_review(connection, document_id)
                if (
                    document["revision"] != request.expected_revision
                    or document["review_token"] != request.expected_review_token
                ):
                    raise ConcurrentPublication("document review has changed")
                if (
                    document["status"] not in ("draft", "published")
                    or document["parse_error_code"] is not None
                ):
                    raise ValueError(
                        "Only a successfully extracted draft can be published."
                    )
                if not request.confirmed:
                    raise ValueError("Confirm that you reviewed the included content.")
                # An interrupted ingestion can leave a draft with only some of
                # its chunks. Such a partial document cannot be published.
                expected = chunk_text(document["extracted_text"], load_limits())
                chunks = [
                    KnowledgeReviewChunk.model_validate(c) for c in document["chunks"]
                ]
                if [(c.ordinal, c.heading, c.body) for c in chunks] != [
                    (c.ordinal, c.heading, c.body) for c in expected
                ]:
                    raise ValueError(
                        "Extraction is incomplete. Re-upload the document."
                    )
                # Parsing writes occurrences after each chunk. Refuse a draft
                # observed between those writes rather than publishing a number
                # for which no withholding occurrence has been saved yet.
                for chunk in chunks:
                    expected_figures = Counter(
                        (Decimal(f.value), f.kind, f.surface, f.source_sentence)
                        for f in figures_in(chunk.body)
                    )
                    stored_figures = Counter(
                        (f["value"], f["kind"], f["surface"], f["source_sentence"])
                        for f in document["figures"]
                        if f["chunk_id"] == chunk.id
                    )
                    if expected_figures != stored_figures:
                        raise ValueError(
                            "Figure extraction is incomplete. Re-upload the document."
                        )
                scopes = publication_scopes(
                    chunks, request.selections, {p.id for p in load_inventory()}
                )
                for chunk, scope in zip(document["chunks"], scopes, strict=True):
                    review_id = await connection.fetchval(
                        "INSERT INTO knowledge_chunk_reviews (chunk_id, action, project_id, actor_kind) VALUES ($1, $2, $3, 'admin') RETURNING id",
                        chunk["id"],
                        scope.retrieval_scope,
                        scope.project_id,
                    )
                    await connection.execute(
                        "UPDATE knowledge_chunks SET retrieval_scope=$2, project_id=$3, scope_review_id=$4, prompt_body=$5 WHERE id=$1",
                        chunk["id"],
                        scope.retrieval_scope,
                        scope.project_id,
                        review_id,
                        chunk["body"]
                        if scope.retrieval_scope
                        in ("general_knowledge", "project_knowledge")
                        else None,
                    )
                # Older revisions stop retrieving in the same transaction.
                await connection.execute(
                    "UPDATE knowledge_documents SET status='archived', updated_at=now() WHERE id=$1 AND revision<>$2 AND status='published'",
                    document_id,
                    request.expected_revision,
                )
                await connection.execute(
                    "UPDATE knowledge_documents SET status='published', published_at=now(), updated_at=now() WHERE id=$1 AND revision=$2",
                    document_id,
                    request.expected_revision,
                )
                result = {
                    "status": "published",
                    "revision": request.expected_revision,
                    "request_id": str(request.request_id),
                }
                await connection.execute(
                    "INSERT INTO knowledge_document_publications (request_id, document_id, document_revision, request, result, actor_kind) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,'admin')",
                    request.request_id,
                    document_id,
                    request.expected_revision,
                    json.dumps(payload),
                    json.dumps(result),
                )
                return result

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
                await connection.fetch(
                    "SELECT d.revision FROM knowledge_documents d JOIN knowledge_chunks c ON c.document_id=d.id WHERE c.id=$1 ORDER BY d.revision FOR UPDATE OF d",
                    chunk_id,
                )
                from ambassador.knowledge import review_scope
                from ambassador.inventory import load_inventory

                current = await connection.fetchrow(
                    "SELECT * FROM knowledge_chunks WHERE id=$1", chunk_id
                )
                if current is None:
                    raise LookupError("no such chunk")
                if current["retrieval_scope"] == "inventory_governed" and action in (
                    "general_knowledge",
                    "project_knowledge",
                ):
                    raise ValueError("inventory content remains excluded")
                if current["retrieval_scope"] == "inventory_governed":
                    action = "inventory_governed"
                settled = review_scope(
                    action,
                    project_id=project_id,
                    inventory_project_ids={p.id for p in load_inventory()},
                    conflicts_with_inventory=current["conflict_code"] is not None,
                )
                if settled.conflict_code == "unknown_project":
                    raise ValueError("choose an inventory project")
                action, project_id = settled.retrieval_scope, settled.project_id
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
                await connection.fetch(
                    "SELECT d.revision FROM knowledge_documents d JOIN knowledge_figures c ON c.document_id=d.id WHERE c.id=$1 ORDER BY d.revision FOR UPDATE OF d",
                    figure_id,
                )
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


class NoSuchLead(LookupError):
    """The lead is genuinely absent.

    Named rather than a bare `LookupError` because `KeyError` is one too.
    A route catching `LookupError` to mean "no such lead" turns any
    internal mapping error into a confident 404 about a lead that exists -
    which is exactly what hid the unsealed decision note: the admin was
    told the lead was gone.
    """


class ConcurrentDecision(RuntimeError):
    """A second admin decided while this one was in flight."""


class ConcurrentPublication(RuntimeError):
    """The source or its review changed after the page was read."""
