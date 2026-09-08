-- One explicit document decision, with a stable response for network retries.
CREATE TABLE knowledge_document_publications (
    request_id uuid PRIMARY KEY,
    document_id uuid NOT NULL,
    document_revision integer NOT NULL,
    request jsonb NOT NULL,
    result jsonb NOT NULL,
    actor_kind text NOT NULL CHECK (actor_kind IN ('admin', 'user')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, document_revision)
        REFERENCES knowledge_documents (id, revision)
);
CREATE FUNCTION knowledge_publications_are_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'knowledge document publications are append-only';
END;
$$;
CREATE TRIGGER knowledge_publications_append_only
    BEFORE UPDATE OR DELETE ON knowledge_document_publications
    FOR EACH ROW EXECUTE FUNCTION knowledge_publications_are_append_only();
