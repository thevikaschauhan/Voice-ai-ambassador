"""One guard on the connect path: refuse the transaction pooler.

`DATABASE_URL` is a single Railway variable shared by the worker and the admin
API (ADR-018), and Supavisor offers two ports for the same host:

    5432  session mode      persistent processes, prepared statements work
    6543  transaction mode  short-lived clients; asyncpg needs
                            statement_cache_size=0 and no Connection.prepare()

The admin-api pre-deploy reported success on 6543, because the migration runner
uses short simple-protocol statements and survives a URL the runtime pool
cannot. A signal that cannot tell the two apart is not a check - so the check
lives here, in the path both entry points share, rather than in whoever
remembers to read the port.

FAIL CLOSED, and do not adapt. Setting `statement_cache_size=0` would make the
process tolerate 6543 and quietly become a different deployment than ADR-018
describes; the port gets fixed and the code stays honest.

A DENY-LIST of the one known transaction port, not an allow-list of 5432: a
laptop and CI run 5433, and an allow-list would refuse the database this
repository's own tests use.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Supavisor's transaction-mode port. The only PORT denied, and named as a
# constant because the message has to be able to say it.
TRANSACTION_POOLER_PORT = 6543
SESSION_POOLER_PORT = 5432

# Supabase's direct-connection hostname shape, `db.<project ref>.supabase.co`.
# Denied by SHAPE rather than by resolving anything: the free tier's direct
# endpoint is IPv6-only while the worker's outbound IPv6 is off, so a URL that
# is compliant by port still cannot connect - and it fails as `Network is
# unreachable`, which says nothing about what to change.
_DIRECT_HOST_PREFIX = "db."
_DIRECT_HOST_SUFFIX = ".supabase.co"
POOLER_HOST_SUFFIX = "pooler.supabase.com"


class TransactionPoolerRefused(RuntimeError):
    """DATABASE_URL points at Supavisor transaction mode."""


class DirectConnectionRefused(RuntimeError):
    """DATABASE_URL points at Supabase's direct (IPv6-only) endpoint."""


def dsn_port(dsn: str) -> int | None:
    """The port a DSN names, or None when it leaves Postgres' default.

    Never raises on a malformed DSN: this guard's job is to refuse ONE known
    wrong value, and turning a parse quirk into a startup failure would make it
    the thing that broke the deploy.
    """
    try:
        return urlsplit(dsn).port
    except ValueError:
        return None


def _is_direct_host(host: str | None) -> bool:
    if not host:
        return False
    lowered = host.lower()
    return lowered.startswith(_DIRECT_HOST_PREFIX) and lowered.endswith(
        _DIRECT_HOST_SUFFIX
    )


def assert_session_mode(dsn: str) -> int | None:
    """Refuse transaction mode and the direct endpoint.

    Returns the port, for the caller's log line.
    """
    try:
        host = urlsplit(dsn).hostname
    except ValueError:
        host = None
    if _is_direct_host(host):
        # The SHAPE, never the host: a Supabase hostname carries the project
        # ref, and the DSN carries a password.
        raise DirectConnectionRefused(
            "DATABASE_URL is a Supabase direct-connection host, which is "
            f"IPv6-only; use the session pooler ({POOLER_HOST_SUFFIX}) on port "
            f"{SESSION_POOLER_PORT}."
        )
    port = dsn_port(dsn)
    if port == TRANSACTION_POOLER_PORT:
        raise TransactionPoolerRefused(
            f"DATABASE_URL uses port {TRANSACTION_POOLER_PORT} (Supavisor "
            f"transaction mode); this process needs session mode on port "
            f"{SESSION_POOLER_PORT}."
        )
    return port


def session_mode_line(dsn: str) -> str:
    """The one startup line. The PORT and nothing else.

    Not the host, not the user, and above all not the DSN, which carries the
    password. The port is the whole of what was wrong and the whole of what a
    reader needs to confirm it was fixed.
    """
    port = dsn_port(dsn)
    return f"database port {port if port is not None else 'default'} (session mode)"
