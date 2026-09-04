"""The field-path vocabulary, in one place because two services share it.

A field path is the AAD of the envelope it seals, so it is not a label - it
is part of the key material. `seal(lead_id, "turns.3", ...)` and
`open(lead_id, "turns.3.payload", ...)` do not disagree about a name, they
disagree about a cipher input, and the second one cannot open the first.

That happened. The worker sealed `turns.{i}`, the admin API opened
`turns.{i}.payload`, and every turn of a real call was unreadable in the
admin surface while both test suites stayed green - each side had tested
itself against its own spelling. Two f-strings in two repositories-worth of
code, with nothing that fails when they stop agreeing, is the whole defect.

So: one function per field, and `test_no_module_builds_a_field_path_from_a_literal`
fails on a literal passed to `seal`, `open` or `open_field` anywhere in
`adapter/`.

THE VALUES ARE FROZEN. Every one of these strings is the AAD of data already
written to the production database, so changing a value is not a rename - it
makes existing rows permanently unreadable. `turn_payload` returns
`turns.{i}` and not the more descriptive `turns.{i}.payload` for exactly that
reason: the writer's spelling is the one on disk, so the reader is what had
to move. If a path ever must change, it needs a migration that re-seals every
affected row under the new name, not an edit here.
"""

from __future__ import annotations

BRIEF = "brief"
SUMMARY = "summary"


def brief() -> str:
    """The last accepted `LeadBrief` for the call."""
    return BRIEF


def summary() -> str:
    """The analysis summary, written by the finaliser rather than the writer."""
    return SUMMARY


def contact(field: str) -> str:
    """One captured contact value: `name`, `phone` or `email`."""
    return f"contact.{field}"


def turn_payload(turn_index: int | str) -> str:
    """One turn's full-fidelity record.

    `turns.{i}` - the value the worker has always written, and therefore the
    value on disk for every lead already persisted.
    """
    return f"turns.{turn_index}"


def decision_note(sequence: int | str) -> str:
    """An admin's note on one decision, by its per-lead sequence number."""
    return f"decisions.{sequence}.note"
