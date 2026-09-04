-- `buyer_farewell_repeated` joins the recordable endings.
--
-- The adapter could already produce it: #98's repeated-farewell path assigns
-- _call_end_reason='buyer_farewell_repeated', while `CallEndReason` and this
-- CHECK listed five values without it. So a call that ended on a double
-- goodbye - an ordinary, polite ending - failed at validation, and would have
-- failed at INSERT here too. That is after the call is over and the buyer is
-- gone, which makes it a lost lead rather than a retryable error.
--
-- A new file rather than an edit to 0001: that file is merged and applied, so
-- changing it would mean the schema a deployed database carries no longer
-- matches the file that claims to describe it.
--
-- The CHECK is DROPPED and RE-CREATED rather than widened in place, because
-- Postgres has no ALTER for a CHECK's expression. 0001 declared it inline, so
-- it carries the name Postgres generates for that - leads_call_end_reason_check
-- - and this re-creates it under the same name so a later migration can find
-- it where it expects to. Existing rows all hold one of the five older values,
-- which the new constraint still accepts, so the ALTER validates without a
-- rewrite.
ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_call_end_reason_check;

ALTER TABLE leads ADD CONSTRAINT leads_call_end_reason_check
    CHECK (call_end_reason IN (
        'buyer_farewell', 'buyer_farewell_repeated', 'agent_farewell',
        'duration_cap', 'buyer_left', 'session_error'));
