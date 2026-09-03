"""The read side of the PII envelope, and the audit of every decrypt.

P2-S12's RED test lives here. Dwight's #111 built the WRITE side: AES-256-GCM
with the lead id and field path as associated data, keyed HMAC fingerprints,
and a key version in the envelope. This card is what happens when the admin
API reads it back.

The property under test is a pair, and neither half is sufficient. The stored
bytes must not be the buyer's words, AND the authenticated API must return
those words - an encryption test that only checks the first half passes on an
API that can never decrypt anything, which is a broken product with excellent
confidentiality.

The third thing, and the reason the card exists at all: the ENVELOPE must not
reach the client. docs/02- says the API returns the ordinary domain shape only
after authentication and that the persistence envelope never leaks into the web
contract. Today it does - `get_lead` hands the raw dict straight out - so a web
tier would be parsing nonces.

Gated on DATABASE_URL_TEST like the other route tests. Keys here are TEST
values; the live ones are never printed, logged or asserted against - the only
thing this file checks about a real key is its SHAPE.
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

TOKEN = "a-shared-admin-token-for-pii-tests"

# Test keys. Any string of 32+ characters is accepted because the real key is
# DERIVED from it by HKDF rather than parsed as key material (#111), so these
# need no particular encoding - they only need to be long enough.
ENCRYPTION_KEY = "test-encryption-key-padded-to-thirty-two-plus"
HASH_KEY = "test-hash-key-also-padded-to-thirty-two-plus"

BUYER_WORDS = "my budget is two million dirhams for a Skyrise studio"
BUYER_NAME = "A Buyer"
BUYER_PHONE = "+971500000042"
SUMMARY = "The buyer asked about a Skyrise studio and stated a budget."


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("PII_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("PII_HASH_KEY", HASH_KEY)
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)


@pytest.fixture
async def database() -> str:
    import asyncpg

    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_pii_{uuid.uuid4().hex[:12]}"
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
async def sealed(database):
    """A lead whose buyer-derived fields are sealed the way the worker seals
    them - through the same Sealer, with the same AAD."""
    from adapter.crypto import Sealer
    from adapter.repository import Repository

    sealer = Sealer.from_env()
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
        payload=sealer.seal(lead_id, "turns.1.payload", BUYER_WORDS.encode()),
    )
    await repository.put_analysis(
        lead_id,
        status="complete",
        summary=sealer.seal(lead_id, "summary", SUMMARY.encode()),
        score_total=30,
        score_version="test-1",
        breakdown=[],
    )
    await repository.put_contact(
        lead_id,
        status="captured",
        asked_turn_index=1,
        source_turn_index=1,
        name=sealer.seal(lead_id, "contact.name", BUYER_NAME.encode()),
        phone=sealer.seal(lead_id, "contact.phone", BUYER_PHONE.encode()),
        email=None,
        phone_fingerprint=sealer.fingerprint(BUYER_PHONE),
        email_fingerprint=None,
        contact_permission=True,
        confirmed=True,
    )
    try:
        yield repository, lead_id, sealer
    finally:
        await repository.close()


@pytest.fixture
async def client(sealed):
    """`ASGITransport` does not run the lifespan, so the fixture supplies what
    the lifespan would: the repository, the Sealer and an event log. Setting
    only the repository leaves the read path with no keys and every route
    answering 503, which is the app being right and the test being wrong."""
    from httpx import ASGITransport, AsyncClient

    from adapter import admin_api
    from adapter.crypto import Sealer
    from adapter.events import EventLog

    repository, _, _ = sealed
    admin_api.app.state.repository = repository
    admin_api.app.state.sealer = Sealer.from_env()
    admin_api.app.state.log = EventLog(session_id="pii")
    transport = ASGITransport(app=admin_api.app)
    async with AsyncClient(transport=transport, base_url="http://admin") as http:
        yield http


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


async def raw_column(dsn: str, sql: str, *args):
    import asyncpg

    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchval(sql, *args)
    finally:
        await connection.close()


# --- the named RED test ---------------------------------------------------


async def test_buyer_payloads_encrypt_while_phase_2_events_contain_no_buyer_words(
    client, sealed, database
):
    """Both halves of the property, plus the audit stream.

    Stored bytes are not the words, the authenticated API returns them, and no
    Phase 2 event mentions them. Only the first is confidentiality; a test that
    stopped there would pass on an API that can never decrypt, which is a
    product that does not work with excellent security properties.
    """
    from adapter.events import EventLog

    _, lead_id, _ = sealed

    stored = await raw_column(
        database,
        "SELECT (payload).ciphertext FROM lead_turns WHERE lead_id = $1",
        lead_id,
    )
    assert stored, "no ciphertext stored"
    assert BUYER_WORDS.encode() not in bytes(stored)

    records: list[dict] = []
    log = EventLog(session_id="pii")
    log.add_observer(records.append)
    from adapter import admin_api

    admin_api.app.state.log = log

    response = await client.get(f"/v1/leads/{lead_id}", headers=auth())
    assert response.status_code == 200, response.text
    detail = response.json()

    # The words come back, once, to an authenticated caller.
    assert detail["turns"][0]["payload"] == BUYER_WORDS
    assert detail["summary"] == SUMMARY
    assert detail["contact"]["phone"] == BUYER_PHONE
    assert detail["contact"]["name"] == BUYER_NAME

    # And nothing the audit emitted carries them.
    assert records, "the read emitted no events at all"
    for record in records:
        blob = repr(record)
        for secret in (BUYER_WORDS, BUYER_NAME, BUYER_PHONE, SUMMARY):
            assert secret not in blob, (record["event"], secret)


# --- the envelope must not reach the client -------------------------------


async def test_the_persistence_envelope_never_leaves_the_api(client, sealed):
    """docs/02-: the envelope never leaks into the web contract.

    Today it does - the detail route hands the repository's dict straight out,
    so a web tier receives `nonce` and `ciphertext` and has to know what an
    AEAD envelope is to render a name. The API owns the domain shape.
    """
    _, lead_id, _ = sealed
    body = (await client.get(f"/v1/leads/{lead_id}", headers=auth())).text
    for envelope_field in ("ciphertext", "nonce", "key_version", "aes-256-gcm"):
        assert envelope_field not in body, envelope_field


async def test_the_list_route_returns_no_buyer_value_at_all(client, sealed):
    """The list projection already excludes the columns; this is the belt on
    the same braces, asserted against a lead whose words really are stored."""
    body = (await client.get("/v1/leads", headers=auth())).text
    for secret in (BUYER_WORDS, BUYER_NAME, BUYER_PHONE, SUMMARY):
        assert secret not in body


# --- the AAD is the whole point -------------------------------------------


async def test_a_ciphertext_moved_to_another_lead_does_not_open(client, sealed):
    """The associated data binds the lead id, so a row copied between leads is
    unreadable rather than misattributed.

    This is the failure that matters: without AAD, a bug or a bad restore that
    moved a turn would show one buyer's words under another buyer's name, and
    nothing in the response would say so.
    """
    repository, lead_id, sealer = sealed

    other_id = await repository.start_lead(
        session_id=f"sess_{uuid.uuid4().hex[:8]}",
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="4-records",
        started_at=datetime.now(UTC),
    )
    stolen = sealer.seal(lead_id, "turns.1.payload", BUYER_WORDS.encode())
    await repository.add_turn(
        other_id,
        turn_index=1,
        timestamp=datetime.now(UTC),
        audit_incomplete=False,
        payload=stolen,
    )

    response = await client.get(f"/v1/leads/{other_id}", headers=auth())
    assert response.status_code == 200, response.text
    detail = response.json()
    assert BUYER_WORDS not in response.text
    # Reported as unreadable rather than omitted: a turn that silently vanished
    # would make a tampered record look like a short call.
    assert detail["turns"][0]["payload"] is None
    assert detail["turns"][0]["payload_error"] == "unreadable"


async def test_a_ciphertext_moved_to_another_field_does_not_open(client, sealed):
    """The AAD binds the field path too, so a summary cannot be served as a
    contact name."""
    repository, lead_id, sealer = sealed

    await repository.put_analysis(
        lead_id,
        status="complete",
        summary=sealer.seal(lead_id, "contact.name", SUMMARY.encode()),
        score_total=30,
        score_version="test-1",
        breakdown=[],
    )
    response = await client.get(f"/v1/leads/{lead_id}", headers=auth())
    assert response.status_code == 200
    assert SUMMARY not in response.text
    assert response.json()["summary"] is None


async def test_an_unreadable_envelope_is_audited_as_a_failure(client, sealed):
    """A decrypt that fails is the single most interesting thing this service
    can observe - it means tampering, a restore across leads, or a key that
    moved - so it cannot be the one event nobody emits."""
    from adapter import admin_api
    from adapter.events import EventLog

    repository, lead_id, sealer = sealed
    other_id = await repository.start_lead(
        session_id=f"sess_{uuid.uuid4().hex[:8]}",
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="4-records",
        started_at=datetime.now(UTC),
    )
    await repository.add_turn(
        other_id,
        turn_index=1,
        timestamp=datetime.now(UTC),
        audit_incomplete=False,
        payload=sealer.seal(lead_id, "turns.1.payload", BUYER_WORDS.encode()),
    )

    records: list[dict] = []
    log = EventLog(session_id="pii")
    log.add_observer(records.append)
    admin_api.app.state.log = log

    await client.get(f"/v1/leads/{other_id}", headers=auth())

    failures = [r for r in records if r["event"] == "envelope_unreadable"]
    assert failures, [r["event"] for r in records]
    assert failures[0]["field_path"] == "turns.1.payload"
    # The field PATH is structural and safe; the value and the reason string
    # are not, and neither appears.
    assert BUYER_WORDS not in repr(failures[0])
    assert "InvalidTag" not in repr(failures[0])


# --- key versions ---------------------------------------------------------


async def test_an_envelope_sealed_under_an_unknown_key_version_is_refused(
    client, sealed
):
    """A future rotation writes v2. This build holds v1 only, so it must say
    the version is unknown rather than try the key it has and report a generic
    decrypt failure - those are different operator problems with different
    fixes."""
    from adapter.crypto import EnvelopeError, Sealer

    _, lead_id, _ = sealed
    sealer = Sealer.from_env()
    envelope = sealer.seal(lead_id, "summary", SUMMARY.encode())
    envelope["key_version"] = "v2"

    with pytest.raises(EnvelopeError, match="key version"):
        sealer.open(lead_id, "summary", envelope)


async def test_the_key_version_is_recorded_on_every_envelope_written(sealed):
    """Without it a rotation cannot tell which rows still need re-sealing."""
    from adapter.crypto import KEY_VERSION, Sealer

    _, lead_id, _ = sealed
    envelope = Sealer.from_env().seal(lead_id, "summary", b"x")
    assert envelope["key_version"] == KEY_VERSION


# --- fingerprints ---------------------------------------------------------


async def test_a_fingerprint_supports_equality_without_reversing_the_value(sealed):
    """docs/10- is careful that hashing is not encryption and is not presented
    as one. Equal values match, different values do not, and the digest carries
    no part of the input."""
    from adapter.crypto import Sealer

    sealer = Sealer.from_env()
    digest = sealer.fingerprint(BUYER_PHONE)
    assert digest == sealer.fingerprint(BUYER_PHONE)
    assert digest != sealer.fingerprint("+971500000043")
    assert BUYER_PHONE not in digest
    assert BUYER_PHONE.strip("+")[-4:] not in digest


async def test_the_fingerprint_is_keyed_so_a_stolen_table_is_not_a_rainbow_table(
    sealed, monkeypatch
):
    """An unkeyed SHA-256 of a phone number is reversible by anyone with a
    number range and a laptop. The key is what makes the digest useless without
    it."""
    from adapter.crypto import Sealer

    first = Sealer.from_env().fingerprint(BUYER_PHONE)
    monkeypatch.setenv("PII_HASH_KEY", "a-different-hash-key-padded-past-thirty-two")
    assert Sealer.from_env().fingerprint(BUYER_PHONE) != first


# --- keys are never printed, and are checked by SHAPE only ----------------


async def test_no_response_or_event_can_carry_a_key(client, sealed):
    from adapter import admin_api
    from adapter.events import EventLog

    records: list[dict] = []
    log = EventLog(session_id="pii")
    log.add_observer(records.append)
    admin_api.app.state.log = log

    _, lead_id, _ = sealed
    bodies = [
        (await client.get("/v1/leads", headers=auth())).text,
        (await client.get(f"/v1/leads/{lead_id}", headers=auth())).text,
        (await client.get("/ready", headers=auth())).text,
        (await client.get("/health")).text,
        repr(records),
    ]
    for body in bodies:
        assert ENCRYPTION_KEY not in body
        assert HASH_KEY not in body


def test_a_configured_key_is_checked_by_shape_and_never_by_value():
    """The rule for this card, written as a test so it is not just a promise.

    A real deployment's keys are 43-character base64url strings. Nothing here
    asserts a VALUE, prints one, or writes one to a log: the only property
    checked is that a key is long enough for the derivation to be meaningful,
    which is a fact about configuration rather than about the secret.
    """
    from adapter.crypto import MIN_KEY_CHARACTERS, derive_key

    assert MIN_KEY_CHARACTERS >= 32
    with pytest.raises(ValueError):
        derive_key("too-short", "PII_ENCRYPTION_KEY")
    # A key of sufficient length derives, and the derived material is not the
    # input - which is the point of deriving rather than parsing.
    material = derive_key("x" * MIN_KEY_CHARACTERS, "PII_ENCRYPTION_KEY")
    assert len(material) == 32
    assert ("x" * MIN_KEY_CHARACTERS).encode() not in material


def test_the_derivation_is_bound_to_the_variable_name():
    """Same secret, two variables, two different keys. Without the name in the
    HKDF info, a deployment that set both variables to the same value would be
    encrypting and fingerprinting under one key."""
    from adapter.crypto import derive_key

    secret = "y" * 40
    assert derive_key(secret, "PII_ENCRYPTION_KEY") != derive_key(
        secret, "PII_HASH_KEY"
    )
