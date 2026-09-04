"""Freeze one call into the durable lead (ADR-020, docs/10- Lead finalisation).

Every call becomes a lead - a farewell, a cap, a disconnect. The source is the
in-process full-fidelity turns and the last accepted brief, NOT the redacted
event stream, which deliberately does not carry buyer utterances, model
sentences or most of the brief.

TWO RULES SHAPE EVERY DECISION HERE.

Nothing buyer-derived reaches Postgres in the clear: the brief, the summary,
each turn's payload and the contact values are sealed by `adapter.crypto`
first, bound to their lead and field. Phone and email additionally carry a
keyed fingerprint so two calls from one buyer are findable without indexing the
clear value.

And persistence never blocks a call. It runs after speech has finished, its
timeouts are bounded, and `persist_or_report` swallows every failure into a
classified event carrying an enum stage and code - never an exception string,
never a buyer word. A missing lead is an operations problem; a farewell that
waited on Postgres would be the buyer's problem.
"""

from __future__ import annotations

import time
from typing import Any, Final

from ambassador.schemas import LeadSnapshot

from .config import Settings
from .crypto import Sealer
from .events import EventLog
from .repository import Repository


def _dsn_target(dsn: str) -> str:
    """`host:port`, for one startup line. Never the DSN - it carries the
    password - and useful because the pooler's PORT is the difference between
    session mode and transaction mode (ADR-018), which is a real thing to get
    wrong and an invisible one to diagnose.
    """
    tail = dsn.rsplit("@", 1)[-1]
    return tail.split("/", 1)[0] or "unknown"


async def build_lead_writer(settings: Settings, log: EventLog) -> "LeadWriter | None":
    """The writer for this job, or None with the reason on the stream.

    ABSENCE MUST BE READABLE. The audit that produced this function could only
    tell "not configured" from "not wired" by reading source, so an unset
    DATABASE_URL emits exactly one `lead_store_disabled` and nothing else.

    A DSN with either key missing REFUSES, naming the variable. Configured
    halfway is the dangerous state: it would either write buyer text in the
    clear or fail once per call, and both are worse than not starting.

    A connect failure is not a refusal. The lead store is not on the call path,
    so an unreachable database degrades to a report and the buyer hears
    everything they were going to hear (ADR-018).
    """
    if not settings.database_url:
        log.emit("lead_store_disabled", reason="no_database_url")
        return None
    missing = [
        name
        for name, value in (
            ("PII_ENCRYPTION_KEY", settings.pii_encryption_key),
            ("PII_HASH_KEY", settings.pii_hash_key),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"DATABASE_URL is set but {', '.join(missing)} is not. Buyer-derived "
            "payloads are encrypted before they reach Postgres (docs/10-), so a "
            "lead store without its keys is not a configuration this will run."
        )
    try:
        return await LeadWriter.connect(
            settings.database_url,
            encryption_key=settings.pii_encryption_key,
            hash_key=settings.pii_hash_key,
            log=log,
        )
    except Exception as exc:
        # Reported in the same shape a failed write uses, with `connect` as the
        # stage, so one query finds every lost lead whatever went wrong.
        log.emit("lead_persist_failed", stage="connect", code=_failure_code(exc))
        return None


class LeadWriter:
    """The one writer of a lead. Owns the repository and the sealer together,
    because a repository without a sealer could only write plaintext."""

    def __init__(self, repository: Repository, sealer: Sealer) -> None:
        self.repository = repository
        self._sealer = sealer

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        encryption_key: str,
        hash_key: str,
        log: EventLog | None = None,
    ) -> "LeadWriter":
        # The sealer FIRST, so a process with no key fails before it opens a
        # pool: the alternative ordering gets a working database and no way to
        # write to it safely.
        sealer = Sealer(encryption_key=encryption_key, hash_key=hash_key)
        repository = await Repository.connect(dsn)
        if log is not None:
            log.emit("lead_store_connected", target=_dsn_target(dsn))
        return cls(repository, sealer)

    async def close(self) -> None:
        await self.repository.close()

    async def persist(self, snapshot: LeadSnapshot) -> Any:
        """Write the snapshot. Idempotent on `session_id`.

        The lead row goes in first and the turns after, so a failure part way
        leaves a lead that says a call happened rather than nothing at all -
        which is ADR-020's whole point about a failed analysis.
        """
        lead_id = await self.repository.start_lead(
            session_id=snapshot.session_id,
            language=snapshot.language,
            requested_language=snapshot.requested_language,
            uncertified_fallback=snapshot.uncertified_fallback,
            inventory_version=snapshot.inventory_version,
            started_at=_moment(snapshot.started_at),
        )
        await self.repository.finish_lead(
            lead_id,
            ended_at=_moment(snapshot.ended_at),
            call_end_reason=snapshot.call_end_reason,
            ended_cleanly=snapshot.ended_cleanly,
        )
        await self.repository.set_ambassador_name(lead_id, snapshot.ambassador_name)

        if snapshot.brief is not None:
            await self.repository.put_brief(
                lead_id,
                self._sealer.seal(
                    lead_id, "brief", snapshot.brief.model_dump_json().encode("utf-8")
                ),
            )

        contact = snapshot.contact
        await self.repository.put_contact(
            lead_id,
            status=contact.status,
            asked_turn_index=contact.asked_turn_index,
            source_turn_index=contact.source_turn_index,
            name=self._seal_optional(lead_id, "contact.name", contact.name),
            phone=self._seal_optional(lead_id, "contact.phone", contact.phone),
            email=self._seal_optional(lead_id, "contact.email", contact.email),
            # Recomputed here rather than trusted from the snapshot: the
            # fingerprint is only meaningful if it came from this key.
            phone_fingerprint=(
                self._sealer.fingerprint(contact.phone) if contact.phone else None
            ),
            email_fingerprint=(
                self._sealer.fingerprint(contact.email) if contact.email else None
            ),
            contact_permission=contact.contact_permission,
            confirmed=contact.confirmed,
        )

        for turn in snapshot.turns:
            await self.repository.add_turn(
                lead_id,
                turn_index=turn.turn_index,
                timestamp=_moment(turn.timestamp),
                audit_incomplete=turn.audit_incomplete,
                payload=self._sealer.seal(
                    lead_id,
                    f"turns.{turn.turn_index}",
                    turn.model_dump_json().encode("utf-8"),
                ),
            )
        return lead_id

    async def mark_analysis_failed(self, lead_id: Any) -> None:
        """Record a failed analysis without a summary or a score.

        Separate from the finaliser's own failure path because a TIMEOUT is
        caught by the caller that owns the shutdown budget, not by the analysis
        itself - and a lead left at `pending` after a timeout would be
        indistinguishable from one whose analysis never started.
        """
        try:
            await self.repository.put_analysis(
                lead_id,
                status="failed",
                summary=None,
                score_total=None,
                score_version=None,
                breakdown=None,
            )
        except Exception:
            return None

    async def persist_or_report(
        self, snapshot: LeadSnapshot, *, log: EventLog
    ) -> Any | None:
        """Persist, or say why on the event stream and carry on.

        The return value is the lead id or None, so a caller can tell the
        difference - but no caller may depend on it, because ADR-018 is
        explicit that an unavailable database never blocks the job ending.
        """
        started = time.monotonic()
        try:
            lead_id = await self.persist(snapshot)
        except Exception as exc:
            log.emit(
                "lead_persist_failed",
                stage="write",
                code=_failure_code(exc),
                turns=len(snapshot.turns),
            )
            return None
        log.emit(
            "lead_persisted",
            turns=len(snapshot.turns),
            elapsed_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return lead_id

    def seal(self, lead_id, field_path: str, plaintext: bytes):
        """Seal a value for this lead. Public because the analysis finaliser
        writes the summary, and the alternative was a second sealer."""
        return self._sealer.seal(lead_id, field_path, plaintext)

    def _seal_optional(
        self, lead_id: Any, field_path: str, value: str | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return self._sealer.seal(lead_id, field_path, value.encode("utf-8"))


# OUR vocabulary, not the driver's. An exception class name looks like a code
# and is not one: it is an open set owned by asyncpg, so it would put a
# third-party taxonomy into an audit stream whose whole discipline is that
# every field is enumerated. Four buckets, chosen because they are the four
# different things an operator would DO about it.
_FAILURE_CODES: Final = (
    (
        "unavailable",
        (
            "InterfaceError",
            "ConnectionDoesNotExistError",
            "CannotConnectNowError",
            "ConnectionRefusedError",
            "TooManyConnectionsError",
            "PostgresConnectionError",
        ),
    ),
    ("timeout", ("TimeoutError", "QueryCanceledError")),
    (
        "rejected",
        (
            "CheckViolationError",
            "UniqueViolationError",
            "NotNullViolationError",
            "ForeignKeyViolationError",
            "DataError",
            "RaiseError",
        ),
    ),
    ("schema", ("UndefinedTableError", "UndefinedColumnError", "SchemaOutOfDate")),
    # Not a database failure at all: the record could not be BUILT. Distinct
    # from `rejected`, which is Postgres refusing a value it was handed -
    # this one never reached Postgres, so the operator action is to look at
    # the model and the call, not at the database.
    ("invalid", ("ValidationError", "ValueError", "TypeError", "KeyError")),
)


def _failure_code(exc: BaseException) -> str:
    """One of a closed set. Never the exception's message, which can carry a
    DSN, a column value or a buyer word."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    for code, matches in _FAILURE_CODES:
        if names.intersection(matches):
            return code
    return "unknown"


def _moment(value: str) -> Any:
    """An ISO string to a datetime, since the snapshot carries strings and
    Postgres wants an instant."""
    from datetime import datetime

    return datetime.fromisoformat(value)
