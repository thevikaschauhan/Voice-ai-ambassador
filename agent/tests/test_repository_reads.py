"""Every read in the repository runs against the real schema, with a row.

A named-column typo is invisible to everything except Postgres. asyncpg
prepares lazily, so `SELECT content_sha` costs nothing until a row exists to
return - and until knowledge ingestion landed, nothing could create a chunk.
The admin-API route tests passed because their repository was a fake that
agreed to any column name, and the container proof passed because the table
was empty. The first real chunk 500s.

So these tests write through the repository's own writers and read back
through its readers, against a real Postgres. A fake cannot hold this
property, because the property IS agreement with the schema.

`test_every_sql_statement_prepares_against_the_real_schema` is the general
form: it asks Postgres to parse every statement in the module, so a query no
test exercises yet still cannot name a column that does not exist. The
round-trip tests below stay because preparing proves a statement is legal,
not that the value written comes back.

Imports are inside each test so a RED run reads N failed = N cases rather
than one collection error.
"""

from __future__ import annotations

import ast
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST"),
    reason="DATABASE_URL_TEST is not set; see tests/test_migrations.py",
)

REPOSITORY = Path(__file__).resolve().parents[1] / "src" / "adapter" / "repository.py"


def _envelope(marker: bytes) -> dict[str, object]:
    """Shaped like a sealed value, without a sealer.

    These reads never decrypt, so the bytes need only survive the round trip.
    """
    return {
        "algorithm": "AES-256-GCM",
        "key_version": "v1",
        "nonce": b"\x00" * 12,
        "ciphertext": marker,
    }


@pytest.fixture
async def database() -> str:
    from adapter.migrations import apply_migrations

    admin_dsn = os.environ["DATABASE_URL_TEST"]
    name = f"amb_reads_{uuid.uuid4().hex[:10]}"
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
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = $1",
                name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()


@pytest.fixture
async def repository(database: str):
    from adapter.repository import Repository

    repo = await Repository.connect(database)
    try:
        yield repo
    finally:
        await repo.close()


@pytest.fixture
async def document(repository):
    """A published-shape document with one chunk and one figure on it."""
    document_id = await repository.add_document(
        revision=1,
        title="Canal Residences brochure",
        source_type="pdf",
        original_filename="canal.pdf",
        mime_type="application/pdf",
        source_bytes=2048,
        source_sha256="a" * 64,
        extracted_text="Handover is in 2027.",
    )
    chunk_id = await repository.add_chunk(
        document_id,
        document_revision=1,
        ordinal=0,
        heading="Handover",
        body="Handover is in 2027.",
        content_sha256="b" * 64,
    )
    figure_id = await repository.add_figure(
        document_id,
        document_revision=1,
        chunk_id=chunk_id,
        value="2027",
        kind="year",
        surface="2027",
        source_sentence="Handover is in 2027.",
        page=3,
    )
    return {"document_id": document_id, "chunk_id": chunk_id, "figure_id": figure_id}


@pytest.fixture
async def lead(repository):
    """A finished lead carrying a turn, an analysis and an audit event."""
    lead_id = await repository.start_lead(
        session_id=f"sess-{uuid.uuid4().hex[:8]}",
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="2026-09-01.1",
        started_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    await repository.add_turn(
        lead_id,
        turn_index=0,
        timestamp=datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc),
        audit_incomplete=False,
        payload=_envelope(b"turn-zero"),
    )
    await repository.put_analysis(
        lead_id,
        status="complete",
        summary=_envelope(b"summary"),
        score_total=55,
        score_version="2026-09-03.1",
        breakdown=[{"signal": "budget_stated", "awarded": 15}],
    )
    await repository.finish_lead(
        lead_id,
        ended_at=datetime(2026, 9, 3, 12, 4, tzinfo=timezone.utc),
        call_end_reason="buyer_left",
        ended_cleanly=True,
    )
    return lead_id


# -- the general form ---------------------------------------------------


async def test_every_sql_statement_prepares_against_the_real_schema(database):
    """Postgres parses every statement in repository.py.

    Preparing resolves table and column names without running anything, so
    this covers the statements no test has a row for yet. It is the check
    that would have caught `content_sha` on the day it was written.
    """
    connection = await asyncpg.connect(database)
    try:
        statements = [
            (node.lineno, node.value.strip())
            for node in ast.walk(ast.parse(REPOSITORY.read_text()))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip().split(" ")[0].upper()
            in {"SELECT", "INSERT", "UPDATE", "DELETE"}
        ]
        assert len(statements) > 20, "SQL is no longer written as module literals"
        rejected = []
        for lineno, sql in statements:
            try:
                await connection.prepare(sql)
            except asyncpg.PostgresSyntaxError:
                raise
            except asyncpg.PostgresError as exc:
                rejected.append(
                    f"{REPOSITORY.name}:{lineno} {type(exc).__name__}: {exc}"
                )
    finally:
        await connection.close()
    assert rejected == []


# -- knowledge reads ----------------------------------------------------


async def test_get_chunks_returns_the_chunk_that_was_written(repository, document):
    chunks = await repository.get_chunks(document["document_id"], revision=1)
    assert [chunk["id"] for chunk in chunks] == [document["chunk_id"]]


async def test_get_chunks_returns_the_content_hash_under_its_real_name(
    repository, document
):
    """The column is `content_sha256`. `content_sha` is a different column
    that does not exist, and naming it takes the whole read down."""
    (chunk,) = await repository.get_chunks(document["document_id"], revision=1)
    assert chunk["content_sha256"] == "b" * 64


async def test_get_chunks_omits_the_search_vector(repository, document):
    """Named columns exist to keep the tsvector out - it is an index
    artefact with no JSON form. Fixing the hash column must not become
    `SELECT *`."""
    (chunk,) = await repository.get_chunks(document["document_id"], revision=1)
    assert "search_vector" not in chunk


async def test_get_document_returns_the_document_that_was_written(repository, document):
    found = await repository.get_document(document["document_id"], revision=1)
    assert found["title"] == "Canal Residences brochure"
    assert found["source_sha256"] == "a" * 64


async def test_list_documents_returns_the_document_that_was_written(
    repository, document
):
    (found,) = await repository.list_documents()
    assert found["id"] == document["document_id"]
    assert found["status"] == "draft"


async def test_list_documents_omits_the_extracted_text(repository, document):
    """The list names its columns so the transcript never reaches a list
    response."""
    (found,) = await repository.list_documents()
    assert "extracted_text" not in found


async def test_get_figures_returns_the_figure_that_was_written(repository, document):
    (figure,) = await repository.get_figures(document["document_id"], revision=1)
    assert figure["id"] == document["figure_id"]
    assert figure["surface"] == "2027"


async def test_get_chunk_returns_the_single_chunk(repository, document):
    chunk = await repository.get_chunk(document["chunk_id"])
    assert chunk["body"] == "Handover is in 2027."


async def test_get_figure_returns_the_single_figure(repository, document):
    figure = await repository.get_figure(document["figure_id"])
    assert figure["kind"] == "year"


# -- lead reads ---------------------------------------------------------


async def test_list_leads_returns_the_lead_that_was_started(repository, lead):
    (found,) = await repository.list_leads(
        status=None, language=None, limit=50, offset=0
    )
    assert found["id"] == lead
    assert found["score_total"] == 55


async def test_list_leads_omits_every_buyer_column(repository, lead):
    """The projection is the control: a column absent from the SELECT
    cannot reach an admin list response."""
    (found,) = await repository.list_leads(
        status=None, language=None, limit=50, offset=0
    )
    for column in (
        "summary",
        "brief",
        "contact_name",
        "contact_phone",
        "contact_email",
    ):
        assert column not in found


async def test_get_lead_returns_the_lead_that_was_started(repository, lead):
    found = await repository.get_lead(lead)
    assert found["call_end_reason"] == "buyer_left"
    assert found["analysis_status"] == "complete"


async def test_get_turns_returns_the_turn_that_was_added(repository, lead):
    (turn,) = await repository.get_turns(lead)
    assert turn["turn_index"] == 0
    assert turn["payload"]["ciphertext"] == b"turn-zero"


async def test_get_decisions_returns_the_decision_that_was_recorded(repository, lead):
    await repository.record_decision(
        lead,
        new_status="qualified",
        reason_code="ready",
        # The sequence is allocated inside the transaction, so the
        # caller is handed it rather than guessing it.
        seal_note=lambda _sequence: _envelope(b"note"),
        actor_kind="admin",
        actor_id=uuid.uuid4(),
        expected_lead_revision=(await repository.get_lead(lead))["revision"],
    )
    (decision,) = await repository.get_decisions(lead)
    assert decision["new_status"] == "qualified"
    assert decision["sequence"] == 1


async def test_get_knowledge_use_returns_the_row_that_was_recorded(
    repository, lead, document
):
    await repository.record_knowledge_use(
        lead,
        turn_index=0,
        query_fingerprint="handover",
        chunk_refs=[{"chunk_id": str(document["chunk_id"]), "revision": 1}],
        figure_review_ids=[],
        withheld_figure_match=False,
        elapsed_ms=42,
    )
    use = await repository.get_knowledge_use(lead, turn_index=0)
    assert use["elapsed_ms"] == 42
    assert use["chunk_refs"][0]["chunk_id"] == str(document["chunk_id"])


async def test_get_audit_events_returns_the_event_that_was_added(repository, lead):
    await repository.add_audit_event(
        lead, event="lead_detail_read", detail={"by": "admin"}
    )
    (event,) = await repository.get_audit_events(lead)
    assert event["event"] == "lead_detail_read"
    assert event["detail"] == '{"by": "admin"}'


# -- full-text retrieval (ADR-019) --------------------------------------


def a_project_in_inventory() -> str:
    """A project id that is actually in inventory.

    Derived, never restated. The fixture this replaces bound a chunk to
    `binghatti-canal`, which is not an inventory project at all - the raw
    UPDATE wrote the binding directly, so nothing ever checked it, and the
    real publication path refuses it with "Choose an existing inventory
    project."
    """
    from ambassador.inventory import load_inventory

    return sorted(project.id for project in load_inventory())[0]


async def publish_through_the_real_path(repository, *, title, text, actions):
    """Ingest `text` and publish it the way the admin API does.

    `actions` maps a chunk's ordinal to its scope choice: either a scope name
    or a `(scope, project_id)` pair.

    Nothing here writes a status. Every retrieval precondition below - the
    document being published, a chunk carrying a `prompt_body`, a figure
    surface withheld - is whatever the publication transaction leaves behind.
    That is the point: audit #166 found that NO production code published a
    document, and the reason 1900+ green tests never noticed is that the
    fixtures set `status = 'published'` themselves. A fixture that asserts a
    precondition into existence cannot report that nothing produces it.
    """
    from adapter.ingestion import parse_document, store_document
    from ambassador.schemas import KnowledgePublicationRequest

    stored = await store_document(
        repository, title=title, parsed=parse_document(None, None, pasted=text)
    )
    document_id = stored["id"]
    review = await repository.get_document_review(document_id)
    selections = []
    for chunk in review["chunks"]:
        choice = actions[chunk["ordinal"]]
        action, project_id = choice if isinstance(choice, tuple) else (choice, None)
        selections.append(
            {
                "chunk_id": str(chunk["id"]),
                "action": action,
                "project_id": project_id,
            }
        )
    await repository.publish_document(
        document_id,
        KnowledgePublicationRequest(
            request_id=uuid.uuid4(),
            expected_revision=review["revision"],
            expected_review_token=review["review_token"],
            confirmed=True,
            selections=selections,
        ),
    )
    return document_id, {chunk["ordinal"]: chunk["id"] for chunk in review["chunks"]}


HANDBOOK = """# Amenities

The rooftop pool is open to residents and faces the water.

# Aquarise

Aquarise faces the water.

# Internal

Internal margin guidance for the pool."""


@pytest.fixture
async def published(repository):
    """A published document with three chunks: general, bound project, and
    one still closed. Only Postgres can settle what the search returns.

    Reached through ingestion and the publication transaction. The bodies
    carry the words the searches below look for, rather than being edited into
    place afterwards, so what retrieval matches is what publication wrote.
    """
    project_id = a_project_in_inventory()
    document_id, chunks = await publish_through_the_real_path(
        repository,
        title="Handbook",
        text=HANDBOOK,
        actions={
            0: "general_knowledge",
            1: ("project_knowledge", project_id),
            2: "admin_only",
        },
    )
    return {
        "document_id": document_id,
        "project_id": project_id,
        "general": chunks[0],
        "project": chunks[1],
        "closed": chunks[2],
    }


async def test_search_returns_reviewed_prose_and_never_a_closed_chunk(
    repository, published
):
    rows = await repository.search_chunks(["pool"], project_ids=[], limit=4)
    assert [row["id"] for row in rows] == [published["general"]]


async def test_a_draft_document_never_retrieves(repository):
    """Ingested and not published: the state a document is in until somebody
    publishes it, which is the state audit #166 found everything was in."""
    from adapter.ingestion import parse_document, store_document

    await store_document(
        repository,
        title="Handbook",
        parsed=parse_document(None, None, pasted=HANDBOOK),
    )
    assert await repository.search_chunks(["pool"], project_ids=[], limit=4) == []


async def test_republishing_stops_the_superseded_revision_retrieving(
    repository, published
):
    """`publish_document` archives the older revision in the same transaction
    (repository.py: "Older revisions stop retrieving in the same transaction"),
    and nothing else in the suite asserts that it does.

    This is also the only test here in which `d.status = 'published'` is
    load-bearing. Revision 1's chunks keep the `prompt_body` publication gave
    them, so status is the ONLY reason they stop being retrievable - which is
    what the raw flip to `'draft'` was standing in for, except that no
    production path can put a published document back into `'draft'` while
    republishing is exactly how one stops retrieving.
    """
    revised = await _second_revision(
        repository,
        published["document_id"],
        text="# Amenities\n\nThe rooftop terrace is open to residents.",
    )
    assert (await repository.get_document_review(published["document_id"]))[
        "status"
    ] == "published"

    rows = await repository.search_chunks(["pool"], project_ids=[], limit=4)
    assert rows == [], "a superseded revision must stop retrieving"
    rows = await repository.search_chunks(["terrace"], project_ids=[], limit=4)
    assert [row["id"] for row in rows] == [revised], (
        "and the revision that replaced it must retrieve"
    )


async def _second_revision(repository, document_id, *, text):
    """Revision 2 of an existing document, published through the real path.

    `store_document` hardcodes `revision=1` and `add_document` mints a fresh
    id on every call, so the revision-2 row itself is the one statement written
    directly here. Its chunks and figures still go through the repository's own
    writers, and the publication is the real transaction - which is what makes
    revision 1 archived rather than a status this test chose.
    """
    from ambassador.knowledge import chunk_text, load_limits
    from ambassador.schemas import KnowledgePublicationRequest
    from adapter.ingestion import figures_in, parse_document

    parsed = parse_document(None, None, pasted=text)
    await repository._pool.execute(
        """
        INSERT INTO knowledge_documents (
            id, revision, title, source_type, original_filename, mime_type,
            source_bytes, source_sha256, extracted_text, status)
        VALUES ($1, 2, 'Handbook', 'txt', NULL, 'text/plain', $2, $3, $4, 'draft')
        """,
        document_id,
        parsed.source_bytes,
        parsed.source_sha256,
        parsed.text,
    )
    import hashlib

    for chunk in chunk_text(parsed.text, load_limits()):
        chunk_id = await repository.add_chunk(
            document_id=document_id,
            document_revision=2,
            ordinal=chunk.ordinal,
            heading=chunk.heading,
            body=chunk.body,
            content_sha256=hashlib.sha256(chunk.body.encode("utf-8")).hexdigest(),
        )
        for figure in figures_in(chunk.body):
            await repository.add_figure(
                document_id=document_id,
                document_revision=2,
                chunk_id=chunk_id,
                value=figure.value,
                kind=figure.kind,
                currency=figure.currency,
                unit=figure.unit,
                surface=figure.surface,
                source_sentence=figure.source_sentence,
                page=figure.page,
            )
    review = await repository.get_document_review(document_id)
    assert review["revision"] == 2
    await repository.publish_document(
        document_id,
        KnowledgePublicationRequest(
            request_id=uuid.uuid4(),
            expected_revision=2,
            expected_review_token=review["review_token"],
            confirmed=True,
            selections=[
                {"chunk_id": str(c["id"]), "action": "general_knowledge"}
                for c in review["chunks"]
            ],
        ),
    )
    return review["chunks"][0]["id"]


async def test_a_bound_project_chunk_ranks_ahead_of_general_knowledge(
    repository, published
):
    """docs/10-: when the turn's project is known its prose sorts first, and
    general knowledge stays eligible on every turn."""
    rows = await repository.search_chunks(
        ["water"], project_ids=[published["project_id"]], limit=4
    )
    assert rows[0]["id"] == published["project"]
    assert published["general"] in [row["id"] for row in rows], (
        "general knowledge stays eligible"
    )


async def test_figures_for_chunks_reports_approval_from_the_active_review(
    repository, published
):
    """`approved` is derived from `active_approval_id`, so a revocation reads
    as False here without a second query."""
    unapproved = await repository.add_figure(
        published["document_id"],
        document_revision=1,
        chunk_id=published["general"],
        value="1250000",
        kind="amount",
        currency="AED",
        surface="1,250,000",
        source_sentence="Prices start at 1,250,000.",
    )
    (figure,) = await repository.figures_for_chunks([published["general"]])
    assert figure["id"] == unapproved
    assert figure["approved"] is False


# -- a buyer speaks sentences, not keywords (ADR-019) --------------------


@pytest.fixture
async def brochure(repository):
    """One published chunk that answers a real question, so the search can be
    asked the question a buyer would actually ask.

    Published through the real path, which means the four figures in this
    sentence are extracted and withheld from `prompt_body` on the way. The
    search still finds it because `search_vector` is generated from `body`,
    not from `prompt_body` - a distinction a fixture that wrote
    `prompt_body = body` could not have shown.
    """
    _, chunks = await publish_through_the_real_path(
        repository,
        title="Aquarise",
        text=(
            "Aquarise studios start at AED 985,000 with handover in 2027. "
            "The tower has 24 floors and 3 pools."
        ),
        actions={0: "general_knowledge"},
    )
    return chunks[0]


async def test_a_natural_question_finds_the_chunk_that_answers_it(repository, brochure):
    """The defect this replaces: `plainto_tsquery` ANDs every token and the
    `simple` configuration strips no stopwords, so "how much are the Aquarise
    studios and when is handover" required `how` AND `much` AND `are` AND
    `the` to appear in the chunk and matched nothing. A buyer speaks that
    sentence; nobody speaks "Aquarise studios handover"."""
    from adapter.retrieval import content_tokens

    rows = await repository.search_chunks(
        content_tokens("how much are the Aquarise studios and when is handover", "en"),
        project_ids=[],
        limit=4,
    )
    assert [row["id"] for row in rows] == [brochure]


async def test_an_utterance_sharing_no_content_word_finds_nothing(repository, brochure):
    """The fix must not be "OR everything", where one stopword in common
    pulls back the entire corpus."""
    from adapter.retrieval import content_tokens

    rows = await repository.search_chunks(
        content_tokens("what is the weather like today", "en"),
        project_ids=[],
        limit=4,
    )
    assert rows == []


async def test_one_matching_word_out_of_several_is_not_a_hit(repository, brochure):
    """`tower` is in the chunk and nothing else in this question is. A single
    common word matching must not qualify the chunk, or every chunk
    containing an ordinary noun answers every question."""
    from adapter.retrieval import content_tokens

    rows = await repository.search_chunks(
        content_tokens("is the tower in Dubai Marina a penthouse", "en"),
        project_ids=[],
        limit=4,
    )
    assert rows == []


@pytest.mark.parametrize(
    "utterance,language",
    [
        ("ما هي أسعار الاستوديو في أكوارايز", "ar"),
        ("अकवाराइज़ में स्टूडियो की कीमत क्या है", "hi"),
    ],
)
async def test_a_non_english_utterance_tokenises_without_erroring(
    repository, brochure, utterance, language
):
    """Tokenisation only. No copy is authored here and none is asserted: the
    property is that an Arabic or Hindi turn reaches the database and gets an
    answer or an empty list, never an exception into the voice path."""
    from adapter.retrieval import content_tokens

    rows = await repository.search_chunks(
        content_tokens(utterance, language), project_ids=[], limit=4
    )
    assert isinstance(rows, list)


async def test_search_chunks_refuses_an_utterance_where_tokens_are_expected(
    repository,
):
    """A str IS a Sequence[str], so passing one would iterate characters and
    search for single letters - a silent wrong answer rather than an error.
    This guard caught three of this file's own older tests during the fix."""
    with pytest.raises(TypeError):
        await repository.search_chunks("pool", project_ids=[], limit=4)


# -- the query side must tokenise the way the index side does -----------

# One natural sentence per language, checked against `Language` below rather
# than hand-listed: a language missing here is a language whose tokenisation
# nobody compared with Postgres, which is the whole defect this guards.
PARITY_SENTENCES = {
    "en": "How much are the Aquarise studios and when is handover?",
    "ar": "ما هي أسعار الاستوديو في أكوارايز ومتى التسليم؟",
    "hi": "अक्वाराइज़ में स्टूडियो की कीमत क्या है और हैंडओवर कब है?",
    "ru": "Сколько стоят студии в Акварайз и когда сдача?",
    "fr": "Combien coûtent les studios Aquarise et quand est la livraison ?",
    "es": "¿Cuánto cuestan los estudios de Aquarise y cuándo es la entrega?",
    "pt": "Quanto custam os estúdios Aquarise e quando é a entrega?",
    "zh": "Aquarise 的 studio 多少 钱 何时 交房",
    "ja": "Aquarise の studio は いくら 引き渡し は いつ",
    "de": "Was kosten die Aquarise Studios und wann ist die Übergabe?",
}

# zh and ja are SPACED here, and that is a finding rather than a fixture
# convenience. `to_tsvector('simple', ...)` splits on whitespace and neither
# language writes any, so an unspaced Chinese or Japanese sentence becomes ONE
# lexeme in the index and one token from `tokenise` - the two agree, and the
# agreement is worthless: a buyer's question can then only match a chunk whose
# whole passage is the identical run of characters. Parity is therefore NOT
# the property that makes retrieval work in these two languages, and a spaced
# sentence is the only shape in which this test asks a real question of them.
# Segmentation for zh/ja is unsolved here and is out of scope for this card
# (reported to god); ADR-019's reasoning for keeping `simple` on the index
# side is the same reasoning that leaves this open.
UNSEGMENTED_LANGUAGES = frozenset({"zh", "ja"})


def test_every_language_has_a_parity_sentence():
    from typing import get_args

    from ambassador.schemas import Language

    assert set(PARITY_SENTENCES) == set(get_args(Language))


@pytest.mark.parametrize("language", sorted(PARITY_SENTENCES))
async def test_our_tokens_are_the_lexemes_postgres_would_index(database, language):
    """Parity with the index side is the property, not any particular regex.

    The first version used `[^\\W_]+`, which is `\\w` minus underscore, and
    Python's `\\w` does not match combining marks - virama, nukta, the
    matras. So a Devanagari sentence shredded into fragments
    (`['अक', 'इज', 'मत', 'डओवर']`) while Postgres indexed whole words, and a
    Hindi call against a Hindi document could never match. Unpointed Arabic
    survived only because its letters are category Lo; a diacritic splits it
    the same way.

    Comparing against `to_tsvector` rather than against an expected list is
    deliberate: the index is the thing we have to agree with, and it is the
    only authority on what a word is here.
    """
    from adapter.retrieval import tokenise

    sentence = PARITY_SENTENCES[language]
    connection = await asyncpg.connect(database)
    try:
        vector = await connection.fetchval(
            "SELECT to_tsvector('simple', $1)::text", sentence
        )
    finally:
        await connection.close()

    lexemes = {
        part.split(":")[0].strip("'").replace("''", "'")
        for part in vector.split()
        if part.startswith("'")
    }
    assert set(tokenise(sentence)) == lexemes


async def test_a_hindi_sentence_finds_a_hindi_chunk(repository):
    """End to end, because parity is a means and this is the point of it."""
    from adapter.retrieval import content_tokens

    _, chunks = await publish_through_the_real_path(
        repository,
        title="Aquarise HI",
        text="अक्वाराइज़ में स्टूडियो की कीमत 985,000 दिरहम है और हैंडओवर 2027 में है।",
        actions={0: "general_knowledge"},
    )
    chunk_id = chunks[0]

    rows = await repository.search_chunks(
        content_tokens(PARITY_SENTENCES["hi"], "hi"), project_ids=[], limit=4
    )
    assert [row["id"] for row in rows] == [chunk_id]
