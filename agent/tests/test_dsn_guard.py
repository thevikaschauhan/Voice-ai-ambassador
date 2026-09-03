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


# --- and the direct host, which a port-only check waves through ------------
#
# The 15:22Z incident was a 5432 URL - compliant by port - on Supabase's DIRECT
# connection host, which is IPv6-only while the worker's outbound IPv6 is off.
# The pre-deploy failed with `OSError: [Errno 101] Network is unreachable`,
# which is loud but says nothing about what to change.
#
# Still a deny-list: localhost, 127.0.0.1, CI's postgres service and any pooler
# host pass, because the same code runs in CI and on a laptop.

DIRECT_DSN = (
    "postgresql://postgres:hunter2@db.abcdefghijklmnop.supabase.co:5432/postgres"
)
POOLER_DSN = (
    "postgresql://postgres.abcdefghijklmnop:hunter2"
    "@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
)


def test_the_direct_connection_host_is_refused_even_on_5432() -> None:
    from adapter.session_mode import DirectConnectionRefused, assert_session_mode

    with pytest.raises(DirectConnectionRefused):
        assert_session_mode(DIRECT_DSN)


def test_the_direct_refusal_names_the_shape_and_not_the_host() -> None:
    """A hostname carries the project ref, and the DSN carries a password. The
    message says what KIND of host it is and what to use instead."""
    from adapter.session_mode import DirectConnectionRefused, assert_session_mode

    with pytest.raises(DirectConnectionRefused) as raised:
        assert_session_mode(DIRECT_DSN)

    message = str(raised.value)
    assert "pooler.supabase.com" in message
    assert "5432" in message
    for fragment in ("abcdefghijklmnop", "hunter2", "db.abcdefghijklmnop.supabase.co"):
        assert fragment not in message, fragment
    assert len(message.strip().splitlines()) == 1


def test_the_pooler_host_passes_on_the_session_port() -> None:
    from adapter.session_mode import assert_session_mode

    assert assert_session_mode(POOLER_DSN) == 5432


def test_the_pooler_host_is_still_refused_on_the_transaction_port() -> None:
    from adapter.session_mode import TransactionPoolerRefused, assert_session_mode

    with pytest.raises(TransactionPoolerRefused):
        assert_session_mode(POOLER_DSN.replace(":5432/", ":6543/"))


def test_local_and_ci_databases_keep_working() -> None:
    """The reason this is a deny-list. An allow-list of pooler hosts would
    refuse every database this repository's own tests use."""
    from adapter.session_mode import assert_session_mode

    assert assert_session_mode("postgresql://u:p@localhost:5434/postgres") == 5434
    assert assert_session_mode("postgresql://u:p@127.0.0.1:5435/postgres") == 5435
    assert assert_session_mode("postgresql://postgres:postgres@postgres:5432/x") == 5432


def test_the_cli_refuses_the_direct_host_in_one_value_free_line() -> None:
    """Discriminating on purpose. Without the pooler-host assertion this test
    passed BEFORE the guard existed: the CLI reached that hostname, failed on
    the network, printed one line and exited 1 - all four of the weaker
    assertions, for none of the right reasons. It must only pass when the
    REFUSAL is what stopped it."""
    result = _run_cli(DIRECT_DSN)

    assert result.returncode != 0
    assert "pooler.supabase.com" in result.stderr
    assert "Network is unreachable" not in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
    written = result.stdout + result.stderr
    for fragment in ("abcdefghijklmnop", "hunter2", "postgresql://"):
        assert fragment not in written, fragment
