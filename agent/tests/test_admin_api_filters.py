"""`/v1/leads` as a list AND filter route (docs/10-:296, :315).

The merged projection filters on status only, so the admin surface can list
leads and cannot narrow them. This adds language and project_id beside status,
with a fixed 422 for a value outside each enum.

The leaked-column assertions are repeated here rather than assumed. Adding
filters means touching the query, and the query's NAMED COLUMN LIST is the
control that keeps buyer words and contact values off the list page - so the
test that guards it belongs next to the change most likely to break it.

Gated on DATABASE_URL_TEST: a filter is a property of SQL, and a fake
repository returns whatever it was told regardless of the WHERE clause.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import get_args

import pytest

from ambassador.schemas import Language

# Derived, never restated: a fourth language added to the product must reach
# these fixtures the day it lands, and the repo has a test that enforces it.
LANGUAGES = sorted(get_args(Language))

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see this module's docstring",
)

TOKEN = "a-shared-admin-token-for-filter-tests"
BUYER_WORDS = "my budget is two million dirhams"
BUYER_PHONE = "+971500000077"


@pytest.fixture
async def database() -> str:
    import asyncpg

    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_flt_{uuid.uuid4().hex[:12]}"
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
    """Three leads in three languages, one of them carrying buyer words and a
    contact value so the projection assertions are not vacuous."""
    from adapter.repository import Repository

    repository = await Repository.connect(database)
    ids = {}
    for language in LANGUAGES:
        ids[language] = await repository.start_lead(
            session_id=f"sess_{language}_{uuid.uuid4().hex[:8]}",
            language=language,
            requested_language=language,
            uncertified_fallback=False,
            inventory_version="4-records",
            started_at=datetime.now(UTC),
        )
    await repository.put_contact(
        ids["en"],
        status="captured",
        asked_turn_index=1,
        source_turn_index=1,
        name={
            "algorithm": "aes-256-gcm",
            "key_version": "v1",
            "nonce": b"0" * 12,
            "ciphertext": b"A Buyer",
        },
        phone={
            "algorithm": "aes-256-gcm",
            "key_version": "v1",
            "nonce": b"0" * 12,
            "ciphertext": BUYER_PHONE.encode(),
        },
        email=None,
        phone_fingerprint="fp",
        email_fingerprint=None,
        contact_permission=True,
        confirmed=True,
    )
    try:
        yield repository, ids
    finally:
        await repository.close()


@pytest.fixture
async def client(monkeypatch, seeded):
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


async def languages_in(client, query: str = "") -> list[str]:
    response = await client.get(f"/v1/leads{query}", headers=auth())
    assert response.status_code == 200, response.text
    return sorted(row["language"] for row in response.json())


# --- the filters ----------------------------------------------------------


async def test_the_unfiltered_list_returns_every_lead(client):
    """The baseline, so every exclusion below means something."""
    assert await languages_in(client) == LANGUAGES


async def test_the_language_filter_excludes_the_other_languages(client):
    """docs/10-:315 names language as a list field, and a field the admin can
    see but not narrow by is a column they have to scan by eye."""
    assert await languages_in(client, "?language=ar") == ["ar"]
    assert await languages_in(client, "?language=en") == ["en"]


async def test_the_status_filter_still_works_beside_the_new_ones(client, seeded):
    """The filter that already existed, re-asserted because this change
    rewrites the WHERE clause it lives in."""
    repository, ids = seeded
    await repository.record_decision(
        ids["en"],
        new_status="qualified",
        reason_code="ready",
        note=None,
        actor_kind="admin",
        actor_id=None,
        expected_lead_revision=0,
    )
    assert await languages_in(client, "?status=qualified") == ["en"]
    assert await languages_in(client, "?status=unreviewed") == [
        language for language in LANGUAGES if language != "en"
    ]


async def test_status_and_language_narrow_together(client, seeded):
    """Two filters are an AND, not a last-one-wins. A list page sets both the
    moment an admin uses the two dropdowns it has."""
    repository, ids = seeded
    await repository.record_decision(
        ids["ar"],
        new_status="qualified",
        reason_code="ready",
        note=None,
        actor_kind="admin",
        actor_id=None,
        expected_lead_revision=0,
    )
    assert await languages_in(client, "?status=qualified&language=ar") == ["ar"]
    assert await languages_in(client, "?status=qualified&language=en") == []


@pytest.mark.parametrize(
    ("query", "field"),
    [("?language=fr", "language"), ("?status=maybe", "status")],
)
async def test_a_value_outside_the_enum_is_a_fixed_422(client, query, field):
    """Refused rather than ignored. A filter value the API silently dropped
    would show an admin the unfiltered list and let them believe it was
    filtered, which is worse than an error - they would act on it."""
    response = await client.get(f"/v1/leads{query}", headers=auth())
    assert response.status_code == 422, response.text
    assert field in response.text


async def test_an_unknown_filter_name_does_not_widen_the_list(client):
    """A typo'd parameter must not quietly return everything as though it had
    been honoured."""
    assert await languages_in(client, "?langauge=ar") == LANGUAGES


# --- the control this change is most likely to break ----------------------


async def test_no_filtered_response_carries_a_buyer_value(client):
    """The named-column projection is the control, and every query shape has to
    keep it. Repeated here beside the change that rewrites the query rather
    than left in the file that first asserted it."""
    for query in ("", "?language=en", "?status=unreviewed", "?limit=1"):
        response = await client.get(f"/v1/leads{query}", headers=auth())
        assert response.status_code == 200, query
        body = response.text
        for secret in (BUYER_WORDS, BUYER_PHONE, "A Buyer"):
            assert secret not in body, (query, secret)
        for column in (
            "brief",
            "summary",
            "contact_name",
            "contact_phone",
            "contact_email",
            "ciphertext",
            "nonce",
        ):
            assert column not in body, (query, column)


async def test_the_filters_are_still_behind_the_bearer(client):
    """A filter is a route parameter, and adding parameters is how a route
    quietly acquires an unauthenticated path."""
    response = await client.get("/v1/leads?language=en")
    assert response.status_code == 401


# --- the project filter (docs/10-:315) ------------------------------------

# Real ids from `data/inventory.json`, read rather than invented: the filter
# validates against inventory, so a made-up id here would test the refusal
# path while pretending to test the match.
SKYRISE = "binghatti-skyrise"
AQUARISE = "binghatti-aquarise"
CIRCLE = "binghatti-circle"


@pytest.fixture
async def with_projects(seeded):
    """Shortlists written the way the finaliser writes them: through
    `put_analysis`, which intersects against inventory first."""
    repository, ids = seeded
    await repository.put_analysis(
        ids[LANGUAGES[0]],
        status="complete",
        summary=None,
        score_total=40,
        score_version="2026-09-03.1",
        breakdown=None,
        project_ids=[SKYRISE, AQUARISE],
    )
    await repository.put_analysis(
        ids[LANGUAGES[1]],
        status="complete",
        summary=None,
        score_total=20,
        score_version="2026-09-03.1",
        breakdown=None,
        project_ids=[CIRCLE],
    )
    return repository, ids


async def ids_in(client, query: str) -> set[str]:
    response = await client.get(f"/v1/leads{query}", headers=auth())
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


async def test_the_project_filter_returns_only_leads_that_shortlisted_it(
    client, with_projects
):
    _, ids = with_projects
    assert await ids_in(client, f"?project_id={CIRCLE}") == {str(ids[LANGUAGES[1]])}


async def test_a_lead_matches_on_any_id_in_its_shortlist_not_just_the_first(
    client, with_projects
):
    """The column is an ARRAY and the match is containment. A predicate that
    only ever compared the first element would pass the test above and fail
    every buyer who mentioned two towers."""
    _, ids = with_projects
    for project in (SKYRISE, AQUARISE):
        assert await ids_in(client, f"?project_id={project}") == {
            str(ids[LANGUAGES[0]])
        }


async def test_the_list_carries_project_ids_so_the_filter_is_verifiable(
    client, with_projects
):
    """An admin who filters must be able to see WHY a row matched. The ids are
    our own inventory identifiers, already public in the catalogue, and
    nothing about a buyer is recoverable from them - which is why they are in
    the clear here while `shortlist_ids` inside the brief stays sealed."""
    response = await client.get(f"/v1/leads?project_id={CIRCLE}", headers=auth())
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert row["project_ids"] == [CIRCLE]


async def test_a_lead_with_no_analysis_has_an_empty_shortlist_not_null(client):
    """`NOT NULL DEFAULT '{}'` - so the projection has no null branch and the
    web surface never has to distinguish "no projects" from "no column"."""
    response = await client.get("/v1/leads", headers=auth())
    assert response.status_code == 200, response.text
    assert all(row["project_ids"] == [] for row in response.json())


async def test_a_project_id_that_is_not_in_inventory_is_refused_by_name(client):
    """The same rule as the other two filters, for the same reason: a filter
    the API silently dropped would show an admin a list they believed was
    narrowed. Here the empty list is the more dangerous shape - "no leads for
    Binghatti Atlantis" reads as a fact about the business when it is a typo.
    """
    response = await client.get(
        "/v1/leads?project_id=binghatti-atlantis", headers=auth()
    )
    assert response.status_code == 422, response.text
    assert "project_id" in response.text


async def test_a_real_project_nobody_shortlisted_is_an_empty_list_not_a_422(
    client, with_projects
):
    """The other half of the same rule. An id that exists and matched nothing
    is a true answer; only an id that cannot exist is a caller error."""
    response = await client.get(
        f"/v1/leads?project_id={'bugatti-residences'}", headers=auth()
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_the_project_filter_intersects_with_the_other_filters(
    client, with_projects
):
    _, ids = with_projects
    language = LANGUAGES[0]
    assert await ids_in(client, f"?project_id={SKYRISE}&language={language}") == {
        str(ids[language])
    }
    other = LANGUAGES[1]
    assert await ids_in(client, f"?project_id={SKYRISE}&language={other}") == set()


async def test_the_containment_predicate_is_index_able(database, with_projects):
    """Measured, because the natural-reading form is the one that cannot.

    `project_ids @> ARRAY['x']` uses the GIN index; `'x' = ANY(project_ids)`
    is the same filter with the same rows and CANNOT use it - on 60k rows
    that was a bitmap index scan at 7.6ms against a sequential scan of 59,880
    rows at 13.4ms. Nobody reads a WHERE clause and thinks about the plan, so
    this asserts the predicate stays index-able. `enable_seqscan` is off
    because the planner would rightly scan a table this small.
    """
    import asyncpg

    connection = await asyncpg.connect(database)
    try:
        await connection.execute("SET enable_seqscan = off")
        plan = await connection.fetchval(
            "EXPLAIN (FORMAT JSON) SELECT id FROM leads"
            " WHERE project_ids @> ARRAY[$1]::text[]",
            SKYRISE,
        )
    finally:
        await connection.close()
    assert "leads_project_ids" in str(plan)


def test_the_list_query_uses_containment_and_not_any():
    """The predicate above is only worth measuring if the call site keeps it.

    A test table is too small for the planner to choose an index, so no
    end-to-end assertion can prove `list_leads` uses one. This reads the
    query instead: `= ANY(project_ids)` is the same filter, reads more
    naturally for a single id, and cannot use the GIN index - so it is
    exactly what a later simplification would reach for, and nothing else
    would fail.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "adapter" / "repository.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    (function,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_leads"
    ]
    sql = " ".join(
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "FROM leads" in node.value
    )
    assert "project_ids @>" in sql
    assert "ANY(project_ids)" not in sql.replace(" ", "")
