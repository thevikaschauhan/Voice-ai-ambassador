"""Apply the versioned SQL under `agent/migrations/`.

Run once as the admin-api's Railway `preDeployCommand`, before that service
takes traffic - never from the voice worker and never on ordinary startup
(ADR-018). Both processes only CHECK the version at startup, which is
`assert_schema_current` below.

Idempotent by construction: each file's name is its version, applied versions
are recorded in `schema_migrations`, and a file already recorded is skipped.
Each file runs inside one transaction, so a half-applied migration is not a
state this can leave behind.

Deliberately not Alembic. This is plain SQL owned by the repository (ADR-018's
portability constraint), the ordering is a filename, and a migration tool would
be a dependency whose value is the thing we are avoiding: schema generation
from a model.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# `0001_phase2.sql` -> version `0001`. The number orders the run; the rest of
# the name is for humans and may be edited without re-applying anything.
_VERSION = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")


def migration_files(directory: Path | None = None) -> list[tuple[str, Path]]:
    """Every migration, in version order, with the version it declares."""
    source = directory or MIGRATIONS_DIR
    found: list[tuple[str, Path]] = []
    for path in sorted(source.glob("*.sql")):
        match = _VERSION.match(path.name)
        if match is None:
            raise ValueError(
                f"{path.name}: a migration is named <digits>_<lower_snake>.sql, "
                "because the digits are what orders the run."
            )
        found.append((match.group(1), path))
    versions = [version for version, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"two migrations share a version: {sorted(versions)}")
    return found


def latest_version(directory: Path | None = None) -> str:
    files = migration_files(directory)
    if not files:
        raise ValueError("no migrations found; the schema version is undefined")
    return files[-1][0]


async def apply_migrations(dsn: str, directory: Path | None = None) -> list[str]:
    """Apply what has not been applied. Returns the versions it ran."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {
            row["version"]
            for row in await connection.fetch("SELECT version FROM schema_migrations")
        }
        ran: list[str] = []
        for version, path in migration_files(directory):
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            # One transaction per file: a migration that fails half way is not
            # a state anyone should have to diagnose at deploy time.
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)"
                    " ON CONFLICT (version) DO NOTHING",
                    version,
                )
            ran.append(version)
        return ran
    finally:
        await connection.close()


async def current_version(connection: asyncpg.Connection) -> str | None:
    exists = await connection.fetchval("SELECT to_regclass('schema_migrations')")
    if exists is None:
        return None
    return await connection.fetchval(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    )


class SchemaOutOfDate(RuntimeError):
    """The database is not at the version this code was written against."""


async def assert_schema_current(
    connection: asyncpg.Connection, directory: Path | None = None
) -> str:
    """The startup check both Python services run.

    They check and refuse; they do not migrate. A worker that migrated on
    startup would race the admin API's pre-deploy run and apply schema changes
    from whichever process happened to boot first.
    """
    expected = latest_version(directory)
    found = await current_version(connection)
    if found != expected:
        raise SchemaOutOfDate(
            f"the database is at schema version {found!r}, this build expects "
            f"{expected!r}. Migrations run as the admin-api preDeployCommand "
            "(ADR-018); they are not applied by a worker on startup."
        )
    return expected


def _main(argv: list[str] | None = None) -> int:
    """`python -m adapter.migrations up`, the admin-api preDeployCommand.

    Exists as a module entry point because that is the exact string
    `.railway/railway.ts` declares. Without it the command exited 0 and applied
    nothing, which is the worst shape a pre-deploy step can have: the deploy
    reports success over a schema that never moved.

    `up` is the only verb. A `down` would be a second thing a pre-deploy
    command could be asked to do by mistake, against a database whose previous
    state nobody kept.
    """
    import asyncio
    import os

    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["up"]:
        print(
            "usage: python -m adapter.migrations up  (the only verb)",
            file=sys.stderr,
        )
        return 2

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        # One line, because it is read in a deploy log beside everything else.
        print(
            "DATABASE_URL is not set; migrations need the session-pooler URL "
            "(ADR-018), and this step will not guess one.",
            file=sys.stderr,
        )
        return 1

    try:
        applied = asyncio.run(apply_migrations(dsn))
    except Exception as exc:
        # The type and message, never the DSN: it carries the password.
        print(f"migrations failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print(f"schema is up to date at version {latest_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
