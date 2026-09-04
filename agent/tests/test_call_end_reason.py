"""One set of call-end reasons, across the code, the schema, the CHECK and the docs.

Ryan reproduced this by execution: #98's repeated-farewell ending assigns
`_call_end_reason = "buyer_farewell_repeated"`, and `CallEndReason` does not
list it - so `lead_snapshot()` raises `ValidationError` on that ending, and
once the lead path is wired the lead of a call that ended on a double goodbye
is lost. A call ending politely twice is not an exotic path.

The defect is a SET disagreeing with itself in four places, so these tests
compare the places rather than restating a list. The adapter's reasons are
collected by ast from `agent.py`, the way `test_events.py` discovers event
names, because a hand-maintained copy of that list in a test is the fifth
place to forget.

The collector reads DEFAULT parameter values as well as call sites, which is
not a detail: `_close_after_farewell_turn` is called with no reason at all for
an ordinary buyer farewell, so `buyer_farewell` is reachable ONLY through the
default. A collector that looked at call sites alone would report a smaller
set than the code can produce and pass while the bug was live.

Imports are inside each test so a RED run reads N failed = N cases rather than
one collection error.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "src" / "adapter" / "agent.py"
EVENTS = Path(__file__).resolve().parents[1] / "src" / "adapter" / "events.py"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
DOCS = Path(__file__).resolve().parents[2] / "docs" / "02-data-contracts.md"
LEADS_TS = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "admin" / "leads.ts"
)
LEADS_SERVER_TS = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "lib"
    / "admin"
    / "leads.server.ts"
)

# The two methods that put a reason into `_call_end_reason`, directly or by
# handing it to `_close_call`. Everything else called `reason` in this adapter -
# an escalation, a room deletion, a shutdown - is a different word.
CLOSERS = frozenset({"_close_after_farewell_turn", "say_farewell_and_close"})


def reasons_the_adapter_can_assign() -> set[str]:
    """Every literal that can reach `_call_end_reason`, read from the source."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"), filename=str(AGENT))
    found: set[str] = set()

    def constant(node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "_call_end_reason":
                value = constant(getattr(node, "value", None))
                if value is not None:
                    found.add(value)

        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in CLOSERS:
                arguments = node.args
                positional = arguments.posonlyargs + arguments.args
                # Positional defaults are right-aligned with their parameters;
                # keyword-only ones are already paired, with None for those
                # that have no default.
                paired = {
                    argument.arg: default
                    for argument, default in zip(
                        positional[len(positional) - len(arguments.defaults) :],
                        arguments.defaults,
                        strict=True,
                    )
                }
                paired.update(
                    {
                        argument.arg: default
                        for argument, default in zip(
                            arguments.kwonlyargs, arguments.kw_defaults, strict=True
                        )
                    }
                )
                assert "reason" in {a.arg for a in positional + arguments.kwonlyargs}, (
                    f"{node.name} no longer takes a reason"
                )
                value = constant(paired.get("reason"))
                if value is not None:
                    found.add(value)

        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else None
            if name in CLOSERS:
                for keyword in node.keywords:
                    if keyword.arg == "reason":
                        value = constant(keyword.value)
                        if value is not None:
                            found.add(value)
                if node.args:
                    value = constant(node.args[0])
                    if value is not None:
                        found.add(value)
    return found


def test_every_reason_the_adapter_can_assign_builds_a_lead_snapshot() -> None:
    """The defect itself: an ending the adapter produces must be recordable."""
    from ambassador.schemas import CallEndReason, LeadSnapshot
    from typing import get_args

    reasons = reasons_the_adapter_can_assign()
    # Guard the collector before trusting it: if the ast walk silently found
    # nothing, every assertion below would pass vacuously.
    assert "buyer_farewell" in reasons, "the default reason must be collected"
    assert "buyer_farewell_repeated" in reasons, "#98's ending must be collected"
    assert len(reasons) >= 5, reasons

    unrecordable = sorted(reasons - set(get_args(CallEndReason)))
    assert not unrecordable, (
        f"the adapter can end a call with {unrecordable} and LeadSnapshot cannot"
        " record it, so that lead is lost at validation"
    )

    for reason in sorted(reasons):
        snapshot = LeadSnapshot(
            session_id=f"session-{reason}",
            started_at="2026-09-04T10:00:00Z",
            ended_at="2026-09-04T10:04:00Z",
            call_end_reason=reason,
            ended_cleanly=True,
            language="en",
            requested_language="en",
            uncertified_fallback=False,
            inventory_version="2026-09-01",
        )
        assert snapshot.call_end_reason == reason


def test_docs_02_lists_the_same_reason_set_as_the_schema() -> None:
    """docs/02- is the contract; a doc listing five of six is a wrong contract."""
    from ambassador.schemas import CallEndReason
    from typing import get_args

    line = next(
        line
        for line in DOCS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("call_end_reason")
    )
    # The quoted values on that line, which is how the table writes an enum.
    documented = set(re.findall(r'"([a-z_]+)"', line))
    assert documented, f"no quoted reasons on the docs line: {line!r}"
    expected = set(get_args(CallEndReason))
    assert documented == expected, f"docs {sorted(documented)} vs {sorted(expected)}"


def _leads_ts_block(name: str) -> str:
    """The source of one declaration in `web/src/lib/admin/leads.ts`.

    A parser, not an import: this is the only place in the repo where a Python
    test reads TypeScript, and the alternative - restating the union here - is
    the copy that goes stale. Both callers assert the block is non-empty first,
    so a rename in leads.ts fails loudly instead of passing on nothing found.
    """
    source = LEADS_TS.read_text(encoding="utf-8")
    # A missing declaration is the most likely thing this helper meets - it is
    # how "the web tier does not type this set at all" looks - so it fails with
    # the name it went looking for rather than with ValueError from `index`.
    assert name in source, f"{LEADS_TS.name} has no declaration `{name}`"
    start = source.index(name)
    # Declarations in that file are separated by a blank line. END_REASON_LABELS
    # is currently the last one, so end-of-file is a real terminator and not a
    # defensive flourish - without it this helper raised ValueError and the
    # labels test "failed" on the parser rather than on the missing label.
    end = source.find("\n\n", start)
    return source[start:] if end == -1 else source[start:end]


def test_the_web_reader_types_the_same_reason_set_as_the_schema() -> None:
    """The reader's copy, which is the one that drifted.

    Found live on 2026-09-04: `CallEndReason` in leads.ts listed five of the
    six, `buyer_farewell_repeated` absent, and TypeScript could not catch it
    because `Record<CallEndReason, string>` is satisfied by five keys when the
    UNION is the wrong copy - the checker was consistent with itself and wrong
    about the world.

    This test lives in the AGENT suite on purpose. The drift has happened twice
    now and both times in the same direction: the Python `Literal` is the
    authority, the writer widens it first, and the reader is what nobody
    remembers. So the guard has to fail in the gate the person doing the
    widening actually runs. It is also the fourth place this file compares
    rather than restates, beside the adapter's ast, the CHECK and docs/02-.
    """
    from typing import get_args

    from ambassador.schemas import CallEndReason

    block = _leads_ts_block("export type CallEndReason")
    typed = set(re.findall(r"'([a-z_]+)'", block))
    assert typed, f"no quoted reasons in the leads.ts union: {block!r}"
    expected = set(get_args(CallEndReason))
    assert typed == expected, (
        f"web types {sorted(typed)} vs schema {sorted(expected)}. The reader "
        "must open what the writer sealed: add the member to the union AND to "
        "END_REASON_LABELS in web/src/lib/admin/leads.ts."
    )


def test_the_web_reader_labels_every_reason_it_types() -> None:
    """A union member with no label renders blank, which is the visible half of
    the same bug: both admin render sites index `END_REASON_LABELS` and React
    prints `undefined` as nothing, so the lead looks like it ended for no
    reason. Asserted against the schema rather than against the union, so this
    still fails if the union is right and the labels are not."""
    from typing import get_args

    from ambassador.schemas import CallEndReason

    block = _leads_ts_block("export const END_REASON_LABELS")
    labelled = set(re.findall(r"^\s{2}([a-z_]+):", block, re.M))
    assert labelled, f"no labels parsed from END_REASON_LABELS: {block!r}"
    missing = sorted(set(get_args(CallEndReason)) - labelled)
    assert not missing, f"END_REASON_LABELS has no label for {missing}"


def test_the_web_reader_types_the_same_contact_status_set_as_the_schema() -> None:
    """The reader's other closed set, guarded before it can drift.

    `contact_status` arrives from `list_leads` and the web tier derives its
    one-bit contact indicator from it by comparing against `'captured'`. That
    comparison was written against a field typed `string | null`, so nothing
    checked the literal: renaming or dropping `captured` in
    `ambassador/schemas.py` would leave TypeScript compiling happily while
    every lead silently rendered as having no contact.

    That failure is worse than the `CallEndReason` one this file already
    guards. A missing END_REASON label rendered BLANK, which looks broken and
    gets reported. A wrong contact comparison renders a PLAUSIBLE answer - "no
    contact" - on every row, and looks fine.
    """
    from typing import get_args

    from ambassador.schemas import ContactStatus

    block = _leads_ts_block("export type ContactStatus")
    typed = set(re.findall(r"'([a-z_]+)'", block))
    assert typed, f"no quoted statuses in the leads.ts union: {block!r}"
    expected = set(get_args(ContactStatus))
    assert typed == expected, (
        f"web types {sorted(typed)} vs schema {sorted(expected)}. The list's "
        "contact indicator is derived from this set in "
        "web/src/lib/admin/leads.server.ts."
    )


def test_the_web_contact_indicator_compares_against_a_real_status() -> None:
    """The magic string, checked against the set rather than by eye.

    The derivation is CORRECT as written - `ambassador/contact.py` records
    `unconfirmed` when a read-back is not confirmed and deliberately keeps no
    number, because "a value the buyer has just contradicted is worse than no
    value" - so only `captured` names a contact a human can act on. What was
    missing is anything that would notice if that word stopped existing.
    """
    from typing import get_args

    from ambassador.schemas import ContactStatus

    source = LEADS_SERVER_TS.read_text(encoding="utf-8")
    compared = set(re.findall(r"contact_status === '([a-z_]+)'", source))
    assert compared, "no contact_status comparison found in leads.server.ts"
    unknown = sorted(compared - set(get_args(ContactStatus)))
    assert not unknown, (
        f"the contact indicator compares against {unknown}, which is not a "
        "ContactStatus - so it can never be true and every lead reads as "
        "having no contact"
    )


def test_the_call_ended_comment_names_every_reason_it_can_carry() -> None:
    """The comment above `call_ended` enumerates the closed set. Keep it true.

    It is worth a test because the comment is the only place that says WHY the
    set is closed - free text from a buyer's goodbye must never reach the event
    stream - and a stale enumeration is how a seventh reason gets added without
    anyone noticing the promise.
    """
    from ambassador.schemas import CallEndReason
    from typing import get_args

    source = EVENTS.read_text(encoding="utf-8")
    start = source.index('"call_duration_cap":')
    block = source[start : source.index('"call_ended":')]
    missing = [reason for reason in get_args(CallEndReason) if reason not in block]
    assert not missing, f"the call_ended comment does not name {missing}"


# Marked per test rather than for the module: the three cases above hold on the
# core-only install (ADR-002), which has no asyncpg and no database, and a
# module-level mark would have skipped them there - silently, which is the worst
# way for a test to be absent. asyncpg is imported inside the database code for
# the same reason.
needs_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)


async def _fresh_database() -> tuple[str, str]:
    import asyncpg

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_endreason_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    return admin_dsn.rsplit("/", 1)[0] + f"/{name}", name


async def _drop_database(name: str) -> None:
    import asyncpg

    admin = await asyncpg.connect(os.environ["DATABASE_URL_TEST"])
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await admin.close()


async def _insert(connection: object, reason: str) -> None:
    await connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO leads (session_id, created_at, call_end_reason, language,"
        " requested_language, inventory_version) VALUES ($1, $2, $3, 'en', 'en', 'v1')",
        f"session-{reason}-{uuid.uuid4().hex[:8]}",
        datetime.now(timezone.utc),
        reason,
    )


@pytest.fixture
async def database() -> str:  # noqa: D401
    """An EMPTY database per test, migrated by the real runner."""
    from adapter.migrations import apply_migrations

    dsn, name = await _fresh_database()
    try:
        await apply_migrations(dsn)
        yield dsn
    finally:
        await _drop_database(name)


@needs_database
async def test_postgres_accepts_every_reason_the_code_can_produce(
    database: str,
) -> None:
    """The CHECK is the other half of the set, and the half that loses the lead.

    A value the schema allows and the database refuses fails at INSERT, which
    is after the call is over and the buyer is gone.
    """
    import asyncpg

    from ambassador.schemas import CallEndReason
    from typing import get_args

    connection = await asyncpg.connect(database)
    try:
        for reason in sorted(
            set(get_args(CallEndReason)) | reasons_the_adapter_can_assign()
        ):
            await _insert(connection, reason)

        # And it is still a constraint, not a dropped one: 0004 re-creates the
        # CHECK, and a migration that merely removed it would pass every line
        # above.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert(connection, "buyer_hung_up_politely")
    finally:
        await connection.close()


@needs_database
async def test_0004_upgrades_a_database_already_at_0003(tmp_path: Path) -> None:
    """The case a fresh migration run cannot cover: an existing database.

    Production is at 0003 with rows in it, so 0004 has to ALTER what is there.
    This applies 0001..0003 with the real runner, then points it at the real
    directory, which must apply exactly 0004 and nothing else.
    """
    import asyncpg

    from adapter.migrations import apply_migrations

    older = tmp_path / "migrations"
    older.mkdir()
    for version in ("0001", "0002", "0003"):
        source = next(MIGRATIONS.glob(f"{version}_*.sql"))
        shutil.copy(source, older / source.name)

    dsn, name = await _fresh_database()
    try:
        assert await apply_migrations(dsn, older) == ["0001", "0002", "0003"]

        connection = await asyncpg.connect(dsn)
        try:
            # A lead already recorded under the old CHECK, so the upgrade has
            # to survive existing rows rather than an empty table.
            await _insert(connection, "buyer_farewell")
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await _insert(connection, "buyer_farewell_repeated")
        finally:
            await connection.close()

        assert await apply_migrations(dsn) == ["0004"]

        connection = await asyncpg.connect(dsn)
        try:
            await _insert(connection, "buyer_farewell_repeated")
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await _insert(connection, "buyer_hung_up_politely")
            assert await connection.fetchval("SELECT count(*) FROM leads") == 2
        finally:
            await connection.close()
    finally:
        await _drop_database(name)


@needs_database
async def test_a_call_that_ended_on_a_double_goodbye_stores_its_lead(
    database: str,
) -> None:
    """The outcome this card is about: the LEAD, not the model that describes it.

    dwight left this case to me on purpose. His guard in
    `test_lead_path_wiring.py` monkeypatches the validation failure, because a
    guard that depended on this bug would pass for the wrong reason the moment
    the enum widened. So this is its other half: the real ending, through the
    real shutdown path, into a real database. Before the widening the shutdown
    emitted `lead_persist_failed stage=snapshot code=invalid` and stored
    nothing - the lead was lost readably, which is better than lost silently
    and is still lost.
    """
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")
    pytest.importorskip("cryptography")

    from adapter.agent import shutdown_session
    from adapter.persist import LeadWriter
    from test_lead_path_wiring import KEY, _agent, _all_leads, _ask, _llm

    agent, log, buf, _ = _agent()
    # Exactly what `agent.py:904` assigns when #98's repeated-farewell path
    # closes the call. The ast case above is what proves that literal reachable;
    # this one takes it the rest of the way.
    agent._call_end_reason = "buyer_farewell_repeated"

    writer = await LeadWriter.connect(database, encryption_key=KEY, hash_key=KEY)
    try:
        await shutdown_session(
            agent=agent,
            log=log,
            llm=_llm(),
            stt_node=None,
            lead_writer=writer,
            ask=_ask(),
        )
        leads = await _all_leads(database)
    finally:
        await writer.close()
    await log.aclose()

    written = buf.getvalue()
    assert len(leads) == 1, (
        "a call that ended on a double goodbye must leave a lead behind"
    )
    assert leads[0]["call_end_reason"] == "buyer_farewell_repeated"
    assert '"stage": "snapshot"' not in written, written
