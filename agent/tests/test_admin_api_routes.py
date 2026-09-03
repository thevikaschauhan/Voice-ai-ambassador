"""The admin API against a real Postgres: the projection and the revision rule.

Two behaviours here cannot be proved against a fake. The lead LIST must not
carry buyer words or contact values, and that is a property of the SQL
projection rather than of the route - a fake repository returns whatever the
test told it to, so it would agree with a route that leaked everything. And the
409 on a moved revision is a real transaction with a real `FOR UPDATE`.

Gated on DATABASE_URL_TEST like tests/test_migrations.py, so a core-only
install (ADR-002) and any checkout without a database skip rather than fail. CI
supplies it from the postgres service container; locally:

    docker run --rm -e POSTGRES_PASSWORD=x -p 5433:5432 -d postgres:16
    export DATABASE_URL_TEST=postgresql://postgres:x@localhost:5433/postgres
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see this module's docstring",
)

TOKEN = "a-shared-admin-token-for-route-tests"

BUYER_WORDS = "my budget is two million dirhams for a Skyrise studio"
BUYER_PHONE = "+971500000001"


def envelope(clear: str) -> dict[str, object]:
    """An `encrypted_envelope` composite, the shape dwight's schema requires.

    The ciphertext here is the CLEAR bytes on purpose: the encryption card is
    separate, and what this file tests is whether the list projection ever
    puts these bytes on the wire. Storing them readable makes the assertion
    real - a projection leak would show the words themselves in the response.
    """
    return {
        "algorithm": "aes-256-gcm",
        "key_version": "test",
        "nonce": b"0" * 12,
        "ciphertext": clear.encode("utf-8"),
    }


@pytest.fixture
async def database() -> str:
    """An empty database per test, the same shape dwight's migration tests
    use - a route test must not pass on somebody else's leftovers."""
    import asyncpg

    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_api_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    dsn = admin_dsn.rsplit("/", 1)[0] + f"/{name}"
    try:
        await apply_migrations(dsn)
        yield dsn
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


@pytest.fixture
async def seeded(database):
    """One lead carrying buyer words and a contact value, so the projection
    test has something real to fail on."""
    from adapter.repository import Repository

    repository = await Repository.connect(database)
    lead_id = await repository.start_lead(
        session_id=f"sess_{uuid.uuid4().hex[:8]}",
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="4-records",
        started_at=datetime.now(UTC),
    )
    await repository.add_turn(
        lead_id,
        turn_index=1,
        timestamp=datetime.now(UTC),
        audit_incomplete=False,
        payload=envelope(BUYER_WORDS),
    )
    await repository.put_contact(
        lead_id,
        status="captured",
        asked_turn_index=1,
        source_turn_index=1,
        name=envelope("A Buyer"),
        phone=envelope(BUYER_PHONE),
        email=None,
        phone_fingerprint="fp-phone",
        email_fingerprint=None,
        contact_permission=True,
        confirmed=True,
    )
    try:
        yield repository, lead_id
    finally:
        await repository.close()


@pytest.fixture
async def client(monkeypatch, seeded):
    """An in-loop ASGI client, not `TestClient`.

    `TestClient` drives the app from a worker thread with its own event loop,
    and the asyncpg pool belongs to pytest's. That mismatch surfaces as
    `InterfaceError: cannot perform operation: another operation is in
    progress` on the second query of a request - which reads like a
    concurrency bug in the repository and is not one. `httpx.ASGITransport`
    runs the app on THIS loop, so the pool is used from the loop that created
    it.
    """
    from httpx import ASGITransport, AsyncClient

    from adapter import admin_api

    repository, _ = seeded
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    admin_api.app.state.repository = repository
    transport = ASGITransport(app=admin_api.app)
    async with AsyncClient(transport=transport, base_url="http://admin") as http:
        yield http


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


async def test_the_lead_list_carries_no_buyer_words_or_contact_values(client, seeded):
    """The projection, against a database that actually holds the words.

    This is why the test needs Postgres: the guarantee is that the SQL never
    selects `brief`, `summary` or the contact columns, and a fake repository
    would return whatever it was told regardless of what the route asked for.
    docs/10- keeps those on the detail page only.
    """
    response = await client.get("/v1/leads", headers=auth())
    assert response.status_code == 200
    rows = response.json()
    assert rows, "no leads returned; the projection assertion would be vacuous"

    body = response.text
    assert BUYER_WORDS not in body
    assert BUYER_PHONE not in body
    for forbidden in (
        "brief",
        "summary",
        "contact_name",
        "contact_phone",
        "contact_email",
    ):
        assert forbidden not in rows[0], forbidden

    # And the operational fields the list page needs ARE there.
    for present in (
        "status",
        "score_total",
        "language",
        "created_at",
        "ended_cleanly",
        "contact_status",
        "revision",
    ):
        assert present in rows[0], present


async def test_the_detail_route_does_carry_the_turns(client, seeded):
    """The complement, so the test above is about the LIST rather than about
    the API refusing to return anything."""
    _, lead_id = seeded
    response = await client.get(f"/v1/leads/{lead_id}", headers=auth())
    assert response.status_code == 200
    detail = response.json()
    assert len(detail["turns"]) == 1
    assert detail["decisions"] == []


async def test_a_decision_appends_history_and_rejects_a_stale_lead_revision(
    client, seeded
):
    """docs/06-'s P2-S04 rule, exercised through the route.

    The second admin does not overwrite the first: they get 409 and have to
    read the decision that already landed. A silent overwrite would leave a
    lead whose status disagrees with its own history and no way to tell which
    was intended.
    """
    _, lead_id = seeded

    first = await client.post(
        f"/v1/leads/{lead_id}/decisions",
        headers=auth(),
        json={
            "new_status": "qualified",
            "reason_code": "ready",
            "expected_lead_revision": 0,
        },
    )
    assert first.status_code == 200, first.text

    stale = await client.post(
        f"/v1/leads/{lead_id}/decisions",
        headers=auth(),
        json={
            "new_status": "rejected",
            "reason_code": "not_interested",
            "expected_lead_revision": 0,
        },
    )
    assert stale.status_code == 409, stale.text

    history = (
        await client.get(f"/v1/leads/{lead_id}/decisions", headers=auth())
    ).json()
    assert len(history) == 1
    assert history[0]["new_status"] == "qualified"
    assert history[0]["previous_status"] == "unreviewed"


async def test_a_decision_on_an_unknown_lead_is_404(client):
    response = await client.post(
        f"/v1/leads/{uuid.uuid4()}/decisions",
        headers=auth(),
        json={
            "new_status": "qualified",
            "reason_code": "ready",
            "expected_lead_revision": 0,
        },
    )
    assert response.status_code == 404


async def test_an_unknown_reason_code_is_refused_before_the_database(client, seeded):
    """The enum is in the request model, so a bad code never reaches a
    transaction: the database's CHECK is the backstop, not the validation."""
    _, lead_id = seeded
    response = await client.post(
        f"/v1/leads/{lead_id}/decisions",
        headers=auth(),
        json={
            "new_status": "qualified",
            "reason_code": "because-i-said-so",
            "expected_lead_revision": 0,
        },
    )
    assert response.status_code == 422


async def test_retrying_an_analysis_that_did_not_fail_is_a_conflict(client, seeded):
    """A fresh lead is `pending`, not `failed`. Retrying a complete analysis
    would discard a score an admin may already have acted on, so only a failed
    one is eligible."""
    _, lead_id = seeded
    response = await client.post(f"/v1/leads/{lead_id}/analysis-retry", headers=auth())
    assert response.status_code == 409


async def test_ready_is_200_against_a_real_database(client):
    assert (await client.get("/ready", headers=auth())).status_code == 200


async def test_the_document_list_is_empty_rather_than_an_error(client):
    """An admin with no documents yet gets an empty list. A 404 here would make
    the knowledge page look broken before it has been used."""
    response = await client.get("/v1/knowledge/documents", headers=auth())
    assert response.status_code == 200
    assert response.json() == []


async def test_an_unknown_document_is_404(client):
    response = await client.get(
        f"/v1/knowledge/documents/{uuid.uuid4()}", headers=auth()
    )
    assert response.status_code == 404
