"""Refuse the transaction pooler, at every connect.

The admin-api pre-deploy printed `schema is up to date at version 0001` today
against port 6543 - Supavisor TRANSACTION mode. The runner survives it because
its statements are short and simple-protocol; the runtime pool would not. A
signal that cannot tell 6543 from 5432 is not a check, and both Python services
read the one `DATABASE_URL`, so the guard belongs in the connect path rather
than in whoever remembers to look.

Deny the ONE known transaction port rather than allow only 5432: an allow-list
would refuse 5433, which is what a laptop and CI actually use.

Imports inside each test, so a RED run reads N failed = N cases.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
TRANSACTION_DSN = (
    "postgresql://someone:hunter2@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
)
SESSION_DSN = TRANSACTION_DSN.replace(":6543/", ":5432/")


def _run_cli(dsn: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(AGENT_DIR / "src")
    environment["DATABASE_URL"] = dsn
    return subprocess.run(
        [sys.executable, "-m", "adapter.migrations", "up"],
        cwd=AGENT_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_cli_refuses_the_transaction_pooler() -> None:
    result = _run_cli(TRANSACTION_DSN)

    assert result.returncode != 0
    assert "6543" in result.stderr
    assert "5432" in result.stderr
    # One line: it is read in a deploy log beside everything else.
    assert len(result.stderr.strip().splitlines()) == 1


def test_the_refusal_leaks_no_dsn() -> None:
    """The message names a PORT. The DSN carries a password, and a deploy log
    is not a place to put one."""
    result = _run_cli(TRANSACTION_DSN)
    written = result.stdout + result.stderr

    for fragment in ("hunter2", "someone", "pooler.supabase.com", "postgresql://"):
        assert fragment not in written, fragment


def test_the_repository_refuses_before_it_connects() -> None:
    """Not after a failed handshake: an unreachable transaction-mode host would
    otherwise fail with a connection error and hide the real reason."""
    import asyncio

    from adapter.repository import Repository
    from adapter.session_mode import TransactionPoolerRefused

    with pytest.raises(TransactionPoolerRefused):
        asyncio.run(Repository.connect(TRANSACTION_DSN))


def test_apply_migrations_refuses_before_it_connects() -> None:
    import asyncio

    from adapter.migrations import apply_migrations
    from adapter.session_mode import TransactionPoolerRefused

    with pytest.raises(TransactionPoolerRefused):
        asyncio.run(apply_migrations(TRANSACTION_DSN))


def test_session_mode_and_a_local_port_both_pass() -> None:
    """5433 is what a laptop and CI use, which is why this is a deny-list of
    the one transaction port and not an allow-list of 5432."""
    from adapter.session_mode import assert_session_mode

    assert assert_session_mode(SESSION_DSN) == 5432
    assert assert_session_mode("postgresql://u:p@localhost:5433/postgres") == 5433
    # No port at all is Postgres' own default, not the transaction pooler.
    assert assert_session_mode("postgresql://u:p@localhost/postgres") is None


def test_the_success_line_names_the_port_and_nothing_else() -> None:
    from adapter.session_mode import session_mode_line

    line = session_mode_line(SESSION_DSN)

    assert line == "database port 5432 (session mode)"
    for fragment in ("hunter2", "someone", "pooler.supabase.com"):
        assert fragment not in line
