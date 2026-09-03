-- Phase 2 durable schema (ADR-018, docs/02- "Phase 2").
--
-- Stock Postgres only. No Supabase extension, no pgvector, no RLS: the store
-- is Postgres that Supabase happens to host, and it has to move to another
-- host without touching the domain contracts.
--
-- Full-text search uses the `simple` configuration deliberately. A language
-- stemmer would have to be chosen per document, and the corpus is Gulf real
-- estate in three languages where the terms that matter - project names, unit
-- types, "AED" - are the ones a stemmer damages.

-- `schema_migrations` is NOT created here. The runner creates it before it
-- applies anything, because it has to read that table to decide what to apply -
-- a migration that created its own ledger could only ever run once.

-- The persistence envelope from docs/02-. A composite type rather than four
-- columns per field: a lead carries five separately encrypted values and the
-- alternative is twenty columns whose names have to stay in step by hand.
CREATE TYPE encrypted_envelope AS (
    algorithm    text,
    key_version  text,
    nonce        bytea,
    ciphertext   bytea
);

-- --- leads ---------------------------------------------------------------

CREATE TABLE leads (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The idempotency key. UNIQUE so a retried write cannot double a lead,
    -- and never contact data (docs/02-).
    session_id            text NOT NULL UNIQUE,
    created_at            timestamptz NOT NULL,
    ended_at              timestamptz,
    call_end_reason       text CHECK (call_end_reason IN (
                              'buyer_farewell', 'agent_farewell', 'duration_cap',
                              'buyer_left', 'session_error')),
    -- False for an unresolved final turn. A lead record that cannot say this
    -- silently mixes complete and truncated calls.
    ended_cleanly         boolean NOT NULL DEFAULT false,
    language              text NOT NULL,
    requested_language    text NOT NULL,
    uncertified_fallback  boolean NOT NULL DEFAULT false,
    inventory_version     text NOT NULL,
    brief                 encrypted_envelope,
    summary               encrypted_envelope,
    analysis_status       text NOT NULL DEFAULT 'pending'
                              CHECK (analysis_status IN ('pending', 'complete', 'failed')),
    score_total           integer CHECK (score_total BETWEEN 0 AND 100),
    score_version         text,
    score_breakdown       jsonb,
    status                text NOT NULL DEFAULT 'unreviewed'
                              CHECK (status IN ('unreviewed', 'qualified', 'rejected')),
    -- Optimistic concurrency. Two admins deciding at once is the case this
    -- exists for, and `record_decision` rejects a stale expectation.
    revision              integer NOT NULL DEFAULT 0,
    retention_expires_at  timestamptz,

    contact_status        text NOT NULL DEFAULT 'not_asked'
                              CHECK (contact_status IN (
                                  'not_asked', 'captured', 'declined', 'unconfirmed')),
    contact_asked_turn_index   integer,
    contact_source_turn_index  integer,
    contact_name          encrypted_envelope,
    contact_phone         encrypted_envelope,
    contact_email         encrypted_envelope,
    -- Keyed HMAC, indexed for equality only: finding the same person twice
    -- must not require decrypting every lead.
    contact_phone_fingerprint  text,
    contact_email_fingerprint  text,
    contact_permission    boolean NOT NULL DEFAULT false,
    contact_confirmed     boolean NOT NULL DEFAULT false,

    -- docs/02-: at least one of phone/email when captured, and both null when
    -- declined. Enforced here because a half-captured contact is worse than
    -- none - it looks like a lead somebody can call.
    CONSTRAINT contact_captured_has_a_value CHECK (
        contact_status <> 'captured'
        OR contact_phone IS NOT NULL OR contact_email IS NOT NULL),
    CONSTRAINT contact_declined_holds_nothing CHECK (
        contact_status <> 'declined'
        OR (contact_phone IS NULL AND contact_email IS NULL AND contact_name IS NULL))
);

CREATE INDEX leads_status_created_at ON leads (status, created_at DESC);
CREATE INDEX leads_phone_fingerprint ON leads (contact_phone_fingerprint)
    WHERE contact_phone_fingerprint IS NOT NULL;
CREATE INDEX leads_email_fingerprint ON leads (contact_email_fingerprint)
    WHERE contact_email_fingerprint IS NOT NULL;

CREATE TABLE lead_turns (
    lead_id           uuid NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    turn_index        integer NOT NULL,
    timestamp         timestamptz NOT NULL,
    audit_incomplete  boolean NOT NULL DEFAULT false,
    payload           encrypted_envelope NOT NULL,
    PRIMARY KEY (lead_id, turn_index)
);

-- --- admin decisions, append-only ----------------------------------------

CREATE TABLE admin_decisions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id                uuid NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    sequence               integer NOT NULL,
    previous_status        text NOT NULL,
    new_status             text NOT NULL CHECK (new_status IN ('qualified', 'rejected')),
    reason_code            text NOT NULL CHECK (reason_code IN (
                               'ready', 'follow_up', 'not_interested',
                               'invalid_contact', 'outside_scope', 'duplicate', 'other')),
    note                   encrypted_envelope,
    actor_kind             text NOT NULL CHECK (actor_kind IN ('admin', 'user')),
    actor_id               uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    expected_lead_revision integer NOT NULL,
    UNIQUE (lead_id, sequence)
);

-- Append-only is a property of the TABLE, not of the code that happens to
-- write it. A future route with a stray UPDATE should fail loudly here.
CREATE FUNCTION admin_decisions_are_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'admin_decisions is append-only (docs/02-)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER admin_decisions_no_update
    BEFORE UPDATE OR DELETE ON admin_decisions
    FOR EACH ROW EXECUTE FUNCTION admin_decisions_are_append_only();

-- --- knowledge -----------------------------------------------------------

CREATE TABLE knowledge_documents (
    id                 uuid NOT NULL DEFAULT gen_random_uuid(),
    -- Revisioned, so a turn can cite the text it actually saw after the
    -- document is replaced (docs/02-).
    revision           integer NOT NULL,
    title              text NOT NULL,
    source_type        text NOT NULL CHECK (source_type IN ('pdf', 'docx', 'txt', 'paste')),
    original_filename  text,
    mime_type          text NOT NULL,
    source_bytes       integer NOT NULL,
    source_sha256      text NOT NULL,
    status             text NOT NULL DEFAULT 'parsing'
                           CHECK (status IN ('parsing', 'draft', 'published', 'failed', 'archived')),
    parse_error_code   text CHECK (parse_error_code IN (
                           'unsupported_type', 'invalid_encoding', 'limit_exceeded',
                           'no_extractable_text', 'malformed')),
    extracted_text     text NOT NULL DEFAULT '',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    published_at       timestamptz,
    tenant_id          uuid,
    PRIMARY KEY (id, revision)
);

CREATE TABLE knowledge_chunks (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        uuid NOT NULL,
    document_revision  integer NOT NULL,
    ordinal            integer NOT NULL,
    heading            text,
    body               text NOT NULL,
    -- Closed by default. An unreviewed chunk is admin data, never prompt data.
    retrieval_scope    text NOT NULL DEFAULT 'admin_only'
                           CHECK (retrieval_scope IN (
                               'admin_only', 'general_knowledge',
                               'project_knowledge', 'inventory_governed')),
    project_id         text,
    scope_review_id    uuid,
    conflict_code      text CHECK (conflict_code IN (
                           'conflicts_with_inventory', 'unknown_project')),
    prompt_body        text,
    page_start         integer,
    page_end           integer,
    content_sha256     text NOT NULL,
    search_vector      tsvector GENERATED ALWAYS AS (
                           to_tsvector('simple', coalesce(heading, '') || ' ' || body)
                       ) STORED,
    FOREIGN KEY (document_id, document_revision)
        REFERENCES knowledge_documents (id, revision) ON DELETE CASCADE,
    UNIQUE (document_id, document_revision, ordinal),
    -- docs/02-: project knowledge needs a project, general knowledge must not
    -- carry one, and neither invariant survives being left to the caller.
    CONSTRAINT project_knowledge_names_a_project CHECK (
        retrieval_scope <> 'project_knowledge' OR project_id IS NOT NULL),
    CONSTRAINT general_knowledge_names_no_project CHECK (
        retrieval_scope <> 'general_knowledge' OR project_id IS NULL),
    -- Prompt-eligibility is a consequence of scope, not a separate decision.
    CONSTRAINT only_reviewed_scopes_reach_the_prompt CHECK (
        prompt_body IS NULL
        OR retrieval_scope IN ('general_knowledge', 'project_knowledge'))
);

CREATE INDEX knowledge_chunks_search ON knowledge_chunks USING gin (search_vector);
CREATE INDEX knowledge_chunks_document ON knowledge_chunks (document_id, document_revision);

CREATE TABLE knowledge_figures (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        uuid NOT NULL,
    document_revision  integer NOT NULL,
    chunk_id           uuid NOT NULL REFERENCES knowledge_chunks (id) ON DELETE CASCADE,
    value              numeric NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('amount', 'percent', 'year', 'count')),
    currency           text,
    unit               text,
    surface            text NOT NULL,
    source_sentence    text NOT NULL,
    page               integer,
    -- A projection of the append-only review history below, not the record of
    -- approval itself.
    active_approval_id uuid,
    FOREIGN KEY (document_id, document_revision)
        REFERENCES knowledge_documents (id, revision) ON DELETE CASCADE
);

CREATE INDEX knowledge_figures_chunk ON knowledge_figures (chunk_id);

CREATE TABLE knowledge_figure_reviews (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    figure_id   uuid NOT NULL REFERENCES knowledge_figures (id) ON DELETE CASCADE,
    action      text NOT NULL CHECK (action IN ('approved', 'revoked')),
    actor_kind  text NOT NULL CHECK (actor_kind IN ('admin', 'user')),
    actor_id    uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_chunk_reviews (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id    uuid NOT NULL REFERENCES knowledge_chunks (id) ON DELETE CASCADE,
    action      text NOT NULL CHECK (action IN (
                    'general_knowledge', 'project_knowledge',
                    'inventory_governed', 'admin_only')),
    project_id  text,
    actor_kind  text NOT NULL CHECK (actor_kind IN ('admin', 'user')),
    actor_id    uuid,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT project_review_names_a_project CHECK (
        action <> 'project_knowledge' OR project_id IS NOT NULL)
);

-- What a turn was ALLOWED to have seen, frozen. Revoking a figure or
-- archiving a document later must not rewrite this (docs/02-).
CREATE TABLE knowledge_use (
    lead_id               uuid NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    turn_index            integer NOT NULL,
    query_fingerprint     text NOT NULL,
    chunk_refs            jsonb NOT NULL,
    figure_review_ids     uuid[] NOT NULL DEFAULT '{}',
    withheld_figure_match boolean NOT NULL DEFAULT false,
    elapsed_ms            integer NOT NULL,
    PRIMARY KEY (lead_id, turn_index),
    FOREIGN KEY (lead_id, turn_index)
        REFERENCES lead_turns (lead_id, turn_index) ON DELETE CASCADE
);

-- --- audit ---------------------------------------------------------------

CREATE TABLE audit_events (
    id          bigserial PRIMARY KEY,
    lead_id     uuid REFERENCES leads (id) ON DELETE CASCADE,
    event       text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_events_lead ON audit_events (lead_id, created_at);
