"""The command Railway actually runs.

`.railway/railway.ts` declares the admin-api preDeployCommand as exactly

    uv run --no-sync python -m adapter.migrations up

and the module it names had no `__main__` block, so that string exited 0 and
migrated nothing. Safe - both processes refuse to start on a stale schema - but
a silent success in a pre-deploy step is the wrong failure: the deploy goes
green and the schema quietly has not moved.

So these run the LITERAL command in a subprocess rather than calling the
function. Calling `apply_migrations` would have passed the whole time.

Imports live inside each test on purpose: a module-level one turns RED into a
single collection error, and a gate that cannot count failures against cases
has to trust the description instead of the output.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

AGENT_DIR = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, "-m", "adapter.migrations", "up"]


def _run(command: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    """The command as Railway runs it: from `agent/`, with `src` importable."""
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    environment["PYTHONPATH"] = str(AGENT_DIR / "src")
    environment.update(env)
    return subprocess.run(
        command,
        cwd=AGENT_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
async def empty_database() -> str:
    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_cli_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    try:
        yield admin_dsn.rsplit("/", 1)[0] + f"/{name}"
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


async def test_the_predeploy_command_actually_migrates(empty_database: str) -> None:
    """The whole point: the exact string from the IaC, against a real empty
    database, leaves the schema at the latest version."""
    from adapter.migrations import latest_version

    result = _run(COMMAND, DATABASE_URL=empty_database)

    assert result.returncode == 0, result.stderr
    connection = await asyncpg.connect(empty_database)
    try:
        applied = await connection.fetchval(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )
    finally:
        await connection.close()
    assert applied == latest_version()


async def test_it_says_what_it_applied(empty_database: str) -> None:
    """A pre-deploy step that prints nothing is indistinguishable from one that
    did nothing - which is the bug this card exists to fix."""
    from adapter.migrations import latest_version

    result = _run(COMMAND, DATABASE_URL=empty_database)

    assert latest_version() in result.stdout


async def test_running_it_twice_is_a_no_op_and_still_succeeds(
    empty_database: str,
) -> None:
    """It runs on every deploy, whether or not the schema changed."""
    first = _run(COMMAND, DATABASE_URL=empty_database)
    second = _run(COMMAND, DATABASE_URL=empty_database)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "up to date" in second.stdout.lower()


async def test_no_database_url_fails_loudly_rather_than_quietly(
    empty_database: str,
) -> None:
    """The failure that made this card. Exiting 0 with nothing done lets a
    deploy report success over a schema that never moved."""
    result = _run(COMMAND)

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr
    # One line, because it is read in a deploy log next to everything else.
    assert len(result.stderr.strip().splitlines()) == 1


async def test_an_unreachable_database_is_a_failure_not_a_pass() -> None:
    """A pre-deploy step must not shrug at a database it cannot reach."""
    result = _run(
        COMMAND,
        DATABASE_URL="postgresql://nobody:nobody@127.0.0.1:1/nothing",
    )

    assert result.returncode != 0
    assert result.stderr.strip()


async def test_the_only_verb_is_up(empty_database: str) -> None:
    """`up` is what the IaC declares. A second verb here would be a second
    thing a pre-deploy command could be asked to do by mistake."""
    result = _run(
        [sys.executable, "-m", "adapter.migrations", "down"],
        DATABASE_URL=empty_database,
    )

    assert result.returncode != 0
