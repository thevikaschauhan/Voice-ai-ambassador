"""The admin API's boundary: the bearer, health, readiness and the SQL rule.

P2-S10's RED test lives here. Everything in this file is pure - a fake
repository, no database - because the boundary has to hold whether or not
Postgres is reachable, and a test that needs a database to prove a route
refuses an unauthenticated caller stops running the day the database is paused.

Route behaviour that genuinely needs Postgres is in
tests/test_admin_api_routes.py, gated on DATABASE_URL_TEST the way dwight's
migration tests are.

TWO STRUCTURAL CHOICES, both about keeping the RED commit countable. Imports
are inside the tests, and the client is a CONTEXTMANAGER rather than a pytest
fixture. A fixture that imports the module under test turns a missing module
into a collection error for every test that uses it, and this commit's whole
property is that the number of FAILURES equals the number of new cases. The
first draft of this file used a fixture and reported 10 failed plus 17 errors
for 27 cases, which is exactly the ambiguity the choice avoids.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src" / "adapter" / "admin_api.py"

TOKEN = "a-shared-admin-token-for-tests"


class FakeRepository:
    """Stands in for dwight's Repository, and records what the routes asked for.

    Deliberately not a mock library: the point is which repository call a route
    makes, and a mock that has to be told what to expect will agree with
    whatever the route happens to do.
    """

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.calls: list[str] = []

    async def ping(self) -> None:
        self.calls.append("ping")
        if not self.reachable:
            raise ConnectionError("database is paused")

    async def list_leads(self, **kwargs):
        self.calls.append("list_leads")
        return []

    async def get_lead(self, lead_id):
        self.calls.append("get_lead")
        return {"id": str(lead_id), "revision": 0, "status": "unreviewed"}

    async def get_turns(self, lead_id):
        self.calls.append("get_turns")
        return []

    async def get_decisions(self, lead_id):
        self.calls.append("get_decisions")
        return []


@contextmanager
def client_for(monkeypatch, *, token: str | None = TOKEN, reachable: bool = True):
    from fastapi.testclient import TestClient

    from adapter import admin_api

    if token is None:
        monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ADMIN_API_TOKEN", token)
    repository = FakeRepository(reachable=reachable)
    admin_api.app.state.repository = repository
    with TestClient(admin_api.app) as test_client:
        test_client.repository = repository  # type: ignore[attr-defined]
        yield test_client


def protected_paths(app) -> list[tuple[str, str]]:
    """Every non-health route, read off the app rather than listed by hand.

    A hand-kept list goes stale the day somebody adds a route, which is exactly
    when this test needs to notice. Derived the way tests/test_events.py
    derives event names from the adapter's source.
    """
    skip = {"/health", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
    found: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path is None or path in skip:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            found.append((method, path))
    return found


def example_path(path: str) -> str:
    """A concrete URL for a templated route. The status under test is 401, so
    the id never has to exist - which is the point: authentication is decided
    before anything is looked up."""
    placeholder = "00000000-0000-0000-0000-000000000000"
    for name in ("lead_id", "document_id", "chunk_id", "figure_id", "id"):
        path = path.replace("{" + name + "}", placeholder)
    return path


# --- the named RED test ---------------------------------------------------


def test_every_non_health_admin_route_refuses_a_missing_or_wrong_bearer(monkeypatch):
    """The boundary, over every route the app actually exposes.

    A wrong token and a missing one are both checked: a guard that compared
    against the empty string would pass the missing case and fail the wrong
    one. And nothing may be looked up before the refusal - a route that reads
    the lead and then checks the bearer has already done the work an
    unauthenticated caller asked for.
    """
    from adapter import admin_api

    paths = protected_paths(admin_api.app)
    assert paths, "no protected routes found; the app exposes nothing to guard"

    with client_for(monkeypatch) as client:
        for method, path in paths:
            url = example_path(path)

            missing = client.request(method, url)
            assert missing.status_code == 401, (method, path, missing.text)

            wrong = client.request(
                method, url, headers={"Authorization": "Bearer not-the-token"}
            )
            assert wrong.status_code == 401, (method, path, wrong.text)

            assert client.repository.calls == [], (method, path)


def test_a_refusal_never_says_whether_a_token_is_configured(monkeypatch):
    """An unset token and a wrong token produce the same answer. Telling them
    apart tells an unauthenticated caller whether the service is configured,
    which is deployment state they have no business learning from a 401."""
    with client_for(monkeypatch) as client:
        wrong = client.get("/v1/leads", headers={"Authorization": f"Bearer {TOKEN}x"})
    with client_for(monkeypatch, token=None) as unconfigured:
        absent = unconfigured.get(
            "/v1/leads", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert wrong.status_code == absent.status_code == 401
    assert wrong.json() == absent.json()


def test_an_unset_token_closes_every_route(monkeypatch):
    """Unset-closed, the rule the web gate follows too. A deployment that
    forgot the variable must refuse rather than serve leads to anyone."""
    from adapter import admin_api

    with client_for(monkeypatch, token=None) as unconfigured:
        for method, path in protected_paths(admin_api.app):
            response = unconfigured.request(
                method,
                example_path(path),
                headers={"Authorization": "Bearer anything"},
            )
            assert response.status_code == 401, (method, path)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic " + TOKEN,
        TOKEN,
        f"bearer {TOKEN}",
        f"Bearer  {TOKEN}",
        f"Bearer {TOKEN} ",
        f"Bearer {TOKEN}extra",
        f"Bearer {TOKEN[:-1]}",
    ],
)
def test_only_an_exact_bearer_token_is_accepted(monkeypatch, header):
    """The shapes a proxy, a copy-paste or an attacker actually produces. The
    trailing space and the truncated token are the two a naive `startswith` or
    prefix comparison would let through."""
    with client_for(monkeypatch) as client:
        response = client.get("/v1/leads", headers={"Authorization": header})
    assert response.status_code == 401, header


def test_the_correct_bearer_is_accepted(monkeypatch):
    """The complement: without it every test above would pass on an app that
    refuses everything."""
    with client_for(monkeypatch) as client:
        response = client.get("/v1/leads", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert "list_leads" in client.repository.calls


def test_the_token_is_compared_in_constant_time():
    """Read from the source, because the property cannot be observed from
    outside. A `==` on a shared secret leaks its length and prefix through
    timing, and this is the secret a public web tier proxies with."""
    source = MODULE.read_text(encoding="utf-8")
    assert "compare_digest" in source, (
        "the bearer must be compared with secrets.compare_digest; a plain == "
        "on a shared secret is a timing oracle"
    )


# --- health and readiness -------------------------------------------------


def test_health_needs_no_bearer_and_touches_no_database(monkeypatch):
    """docs/10-: process liveness only. It stays 200 during a database pause so
    Railway does not restart-loop the service, which means it cannot ask the
    database anything."""
    with client_for(monkeypatch) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert client.repository.calls == []


def test_health_stays_200_when_the_database_is_unreachable(monkeypatch):
    with client_for(monkeypatch, reachable=False) as paused:
        assert paused.get("/health").status_code == 200


def test_ready_requires_the_bearer(monkeypatch):
    """docs/10-'s table protects it: readiness names the database and the
    schema, which is deployment state rather than public information."""
    with client_for(monkeypatch) as client:
        assert client.get("/ready").status_code == 401


def test_ready_is_200_when_the_database_answers(monkeypatch):
    with client_for(monkeypatch) as client:
        response = client.get("/ready", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert "ping" in client.repository.calls


def test_ready_is_503_when_the_database_is_unreachable(monkeypatch):
    """Not-ready rather than an exception. Readiness failing is the normal
    state during a free-tier pause (ADR-018), not an error to surface as one."""
    with client_for(monkeypatch, reachable=False) as paused:
        response = paused.get("/ready", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503


def test_neither_health_nor_ready_returns_counts_or_secrets(monkeypatch):
    """docs/10-: no record counts or secrets. A count is a fact about the
    client's data, and readiness is not the place to publish it."""
    with client_for(monkeypatch) as client:
        bodies = [
            client.get("/health").text,
            client.get("/ready", headers={"Authorization": f"Bearer {TOKEN}"}).text,
        ]
    for body in bodies:
        assert TOKEN not in body
        for leak in ("postgres", "dsn", "password", "count"):
            assert leak not in body.lower(), (leak, body)


# --- rules that are properties of the source, not of a response ------------


def test_no_route_handler_contains_sql():
    """ADR-021: the API owns the domain and the repository owns the database.
    SQL here would be a second place the schema is known, and the two would
    drift the first time a column moved."""
    source = MODULE.read_text(encoding="utf-8").lower()
    for keyword in ("select ", "insert into", "update ", "delete from", "returning "):
        assert keyword not in source, (
            f"{keyword!r} appears in admin_api.py; data access goes through "
            "adapter/repository.py so the schema is known in one place"
        )


def test_the_module_exposes_app_for_the_platform_start_command():
    """The IaC starts this with `uvicorn adapter.admin_api:app`, so the
    attribute name is a deployment contract rather than a preference."""
    from fastapi import FastAPI

    from adapter import admin_api

    assert isinstance(admin_api.app, FastAPI)


def test_fastapi_and_uvicorn_are_main_dependencies():
    """The Dockerfile installs main dependencies only, so a dev-group entry
    builds an image whose start command cannot spawn. Not hypothetical: the
    deployed service crash-looped on `Failed to spawn: uvicorn` before this
    card existed."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    main = " ".join(parsed["project"]["dependencies"]).lower()
    assert "fastapi" in main
    assert "uvicorn" in main


# --- the keep-alive probe -------------------------------------------------


async def test_the_keep_alive_probe_emits_a_classified_event():
    """ADR-018's mitigation for the free tier's inactivity pause. It has to
    emit something, or a probe that silently stopped running would look
    identical to one that is working."""
    from adapter.admin_api import run_database_probe
    from adapter.events import EventLog

    records: list[dict] = []
    log = EventLog(session_id="probe")
    log.add_observer(records.append)

    repository = FakeRepository()
    await run_database_probe(repository, log)

    assert "ping" in repository.calls
    probe = [r for r in records if r["event"] == "database_health_probe"]
    assert probe and probe[0]["reachable"] is True


async def test_the_probe_reports_an_unreachable_database_without_raising():
    """A probe that raises takes the service down over the condition it exists
    to observe. It reports and returns."""
    from adapter.admin_api import run_database_probe
    from adapter.events import EventLog

    records: list[dict] = []
    log = EventLog(session_id="probe")
    log.add_observer(records.append)

    await run_database_probe(FakeRepository(reachable=False), log)

    probe = [r for r in records if r["event"] == "database_health_probe"]
    assert probe and probe[0]["reachable"] is False


def test_the_probe_event_carries_no_exception_string():
    """docs/10-: exception strings are never clear. A driver error can quote a
    DSN, and this event is on the durable stream."""
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("str(exc", "str(error", "repr(exc", "{exc}", "{error}"):
        assert forbidden not in source, forbidden
