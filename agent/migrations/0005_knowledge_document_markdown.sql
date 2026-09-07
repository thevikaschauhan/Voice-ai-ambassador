-- 'md' joins the knowledge source types.
--
-- Markdown is what a brochure summary or a payment-plan note actually arrives
-- as when somebody exports it from their notes, and until now `.md` was
-- refused as `unsupported_type` - a file the admin can see is plain text,
-- rejected with a code that reads like a bug. The parser now produces
-- `source_type = 'md'`, and a value the parser can produce but this CHECK
-- refuses fails at INSERT: after the upload has been read, parsed and chunked,
-- as a 500 on a format the service says it supports.
--
-- A new file rather than an edit to 0001: that file is merged and applied, so
-- changing it would mean the schema a deployed database carries no longer
-- matches the file that claims to describe it.
--
-- The CHECK is DROPPED and RE-CREATED rather than widened in place, because
-- Postgres has no ALTER for a CHECK's expression. 0001 declared it inline, so
-- it carries the name Postgres generates for that - verified against a
-- migrated database as knowledge_documents_source_type_check - and this
-- re-creates it under the same name so a later migration can find it where it
-- expects to. Existing rows all hold one of the four older values, which the
-- new constraint still accepts, so the ALTER validates without a rewrite.
ALTER TABLE knowledge_documents
    DROP CONSTRAINT IF EXISTS knowledge_documents_source_type_check;

ALTER TABLE knowledge_documents ADD CONSTRAINT knowledge_documents_source_type_check
    CHECK (source_type IN ('pdf', 'docx', 'txt', 'paste', 'md'));
