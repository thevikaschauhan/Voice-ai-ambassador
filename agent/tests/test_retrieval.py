"""Retrieval runs once per turn, and what it retrieves is what may be spoken.

docs/10- "Retrieval and the figures gate" and ADR-019. Two properties that
look separate and are not:

- `llm_node` runs again for the same buyer turn on a tool split or a
  preemptive generation. Retrieval must run ONCE and the second pass must
  reuse the same chunks, the same immutable document revisions and the same
  figure set. A second search could rank differently, or race a revocation,
  and then the audit records one set while the buyer heard another.
- The figures a turn may speak are extended ONLY by approved occurrences in
  the chunks that turn actually retrieved (ADR-019's source scoping). A
  retrieval miss extends nothing, which is why a miss fails closed.

They are the same property seen twice, so the named case asserts both: reuse
that rebuilt the figure set would satisfy "ran once" while being exactly the
bug.

Imports are inside each test so a RED run reads N failed = N cases rather
than one collection error.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest

pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

from livekit.agents import llm as lk_llm  # noqa: E402

from test_agent import (  # noqa: E402
    HealthyStream,
    SpyLLM,
    make_settings,
    run_llm_node,
    user_ctx,
)


class SpyRepository:
    """Counts searches, so "once per turn" is measured rather than assumed."""

    def __init__(self, chunks: list[dict[str, Any]], figures: list[dict[str, Any]]):
        self._chunks = chunks
        self._figures = figures
        self.searches: list[dict[str, Any]] = []
        self.knowledge_use: list[dict[str, Any]] = []

    async def search_chunks(self, query, *, project_ids, limit):
        self.searches.append(
            {"query": query, "project_ids": list(project_ids), "limit": limit}
        )
        return list(self._chunks)[:limit]

    async def figures_for_chunks(self, chunk_ids):
        wanted = set(chunk_ids)
        return [f for f in self._figures if f["chunk_id"] in wanted]

    async def record_knowledge_use(self, lead_id, **fields):
        self.knowledge_use.append({"lead_id": lead_id, **fields})


def chunk_row(
    chunk_id: str,
    *,
    body: str = "The tower has a rooftop pool.",
    scope: str = "general_knowledge",
    project_id: str | None = None,
    revision: int = 3,
    document_id: str = "doc-1",
    heading: str | None = "Amenities",
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "document_id": document_id,
        "document_revision": revision,
        "heading": heading,
        "prompt_body": body,
        "retrieval_scope": scope,
        "project_id": project_id,
        "conflict_code": None,
    }


def figure_row(
    figure_id: str,
    chunk_id: str,
    *,
    value: float,
    kind: str = "amount",
    currency: str | None = "AED",
    surface: str = "1,250,000",
    approved: bool = True,
    sentence: str = "Prices start at 1,250,000.",
) -> dict[str, Any]:
    return {
        "id": figure_id,
        "chunk_id": chunk_id,
        "value": value,
        "kind": kind,
        "currency": currency,
        "unit": None,
        "surface": surface,
        "source_sentence": sentence,
        "approved": approved,
    }


def make_agent_with_knowledge(repository, streams):
    from adapter.agent import AmbassadorAgent
    from adapter.events import EventLog
    from adapter.retrieval import KnowledgeRetriever

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    retriever = KnowledgeRetriever(lambda: repository, log=log)
    agent = AmbassadorAgent(settings=make_settings(), log=log, knowledge=retriever)
    spy = SpyLLM(streams)
    agent._llm = spy
    return agent, spy, log, buf


def _sealer():
    from adapter.crypto import Sealer

    # 32 bytes each, as hex. Never a real key: these are test constants and
    # the loader rightly rejects anything it cannot decode.
    return Sealer(encryption_key="0a" * 32, hash_key="1b" * 32)


def _snapshot_with_knowledge_use():
    from ambassador.schemas import (
        KnowledgeUse,
        LeadSnapshot,
        SpokenChunk,
        Timings,
        TurnRecord,
    )

    turn = TurnRecord(
        session_id="sess-1",
        turn_index=1,
        timestamp="2026-09-03T12:00:00+00:00",
        buyer_utterance="What does a studio cost?",
        generated_sentences=["A studio is AED 985,000."],
        spoken_chunks=[SpokenChunk(text="A studio is AED 985,000.", completed=True)],
        guardrail_decisions=[],
        actions=[],
        timings_ms=Timings(total=4200.0),
        inventory_version="10-records",
        model="qwen/qwen3.7-flash",
        prompt_mode="ambassador",
        guardrail_mode="enforce",
    )
    return LeadSnapshot(
        session_id="sess-1",
        started_at="2026-09-03T11:59:00+00:00",
        ended_at="2026-09-03T12:00:10+00:00",
        call_end_reason="buyer_farewell",
        ended_cleanly=True,
        language="en",
        requested_language="en",
        uncertified_fallback=False,
        inventory_version="10-records",
        ambassador_name="Jane",
        turns=[turn],
        knowledge_use=[
            KnowledgeUse(
                turn_index=1,
                query_fingerprint="a1b2c3d4",
                chunk_refs=[{"chunk_id": "c1", "document_id": "doc-1", "revision": 3}],
                figure_review_ids=[],
                withheld_figure_match=False,
                elapsed_ms=12,
            )
        ],
    )


def system_texts(ctx: lk_llm.ChatContext) -> list[str]:
    out = []
    for item in ctx.items:
        if getattr(item, "role", None) == "system":
            content = getattr(item, "content", "")
            out.append(content if isinstance(content, str) else " ".join(content))
    return out


# -- the named case -----------------------------------------------------


async def test_retrieval_runs_once_per_turn_and_reuses_the_same_revision_and_figure_set():
    repository = SpyRepository(
        [chunk_row("c1", revision=3)],
        [figure_row("f1", "c1", value=1250000.0)],
    )
    agent, spy, _, _ = make_agent_with_knowledge(
        repository, [HealthyStream(["A studio "]), HealthyStream(["is available. "])]
    )
    ctx = user_ctx("Tell me about the amenities")

    await run_llm_node(agent, ctx)
    first = agent._knowledge_context
    await run_llm_node(agent, ctx)
    second = agent._knowledge_context

    assert len(repository.searches) == 1, "retrieval ran more than once for one turn"
    assert second is first
    assert [c.document_revision for c in second.chunks] == [3]
    assert second.allowed.currency_amounts == first.allowed.currency_amounts
    assert 1250000.0 in second.allowed.currency_amounts


# -- source scoping (docs/06 P2-S09) ------------------------------------


async def test_only_approved_figures_from_retrieved_chunks_extend_the_turn_set():
    from ambassador.inventory import build_allowed_figures, load_inventory

    base = build_allowed_figures(load_inventory())
    repository = SpyRepository(
        [chunk_row("c1")],
        [
            figure_row("f1", "c1", value=1250000.0, approved=True),
            figure_row("f2", "c1", value=999.0, approved=False),
            figure_row("f3", "c-not-retrieved", value=777.0, approved=True),
        ],
    )
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("what is the price"))

    allowed = agent._knowledge_context.allowed
    assert 1250000.0 in allowed.amounts
    assert 999.0 not in allowed.amounts, "an unapproved occurrence extended the set"
    assert 777.0 not in allowed.amounts, (
        "a figure from an unretrieved chunk extended it"
    )
    assert base.amounts <= allowed.amounts, "the base inventory set must survive"


async def test_a_retrieval_miss_extends_nothing_and_adds_no_system_message():
    from ambassador.inventory import build_allowed_figures, load_inventory

    repository = SpyRepository([], [])
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("something nobody wrote about"))

    base = build_allowed_figures(load_inventory())
    assert agent._knowledge_context.allowed.amounts == base.amounts
    assert system_texts(spy.chat_ctxs[0]) == []


async def test_the_extended_set_is_a_copy_and_the_agent_base_guard_is_untouched():
    """`extend_allowed_figures` returns a copy for this turn only. If the
    agent's own guard were mutated, one turn's document would widen every
    later turn in the call."""
    repository = SpyRepository(
        [chunk_row("c1")], [figure_row("f1", "c1", value=1250000.0)]
    )
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])
    before = agent._guard.allowed

    await run_llm_node(agent, user_ctx("price please"))

    assert agent._guard.allowed is before
    assert 1250000.0 not in before.currency_amounts


# -- what reaches the model ---------------------------------------------


async def test_an_unapproved_figure_is_replaced_before_the_excerpt_reaches_the_model():
    from ambassador.knowledge import WITHHELD_MARKER

    repository = SpyRepository(
        [chunk_row("c1", body="Prices start at 999,000 for a studio.")],
        [
            figure_row(
                "f1",
                "c1",
                value=999000.0,
                surface="999,000",
                approved=False,
                sentence="Prices start at 999,000 for a studio.",
            )
        ],
    )
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("what do studios cost"))

    (message,) = system_texts(spy.chat_ctxs[0])
    assert "999,000" not in message
    assert WITHHELD_MARKER in message


async def test_the_excerpt_names_its_chunk_document_and_revision():
    repository = SpyRepository([chunk_row("c1", document_id="doc-7", revision=4)], [])
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("amenities"))

    (message,) = system_texts(spy.chat_ctxs[0])
    for label in ("c1", "doc-7", "4"):
        assert label in message


async def test_the_excerpt_says_it_is_reference_data_and_not_instructions():
    repository = SpyRepository([chunk_row("c1")], [])
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("amenities"))

    (message,) = system_texts(spy.chat_ctxs[0])
    assert "instruction" in message.lower()


async def test_the_knowledge_message_does_not_accumulate_across_turns():
    """One fixed system message per model call, on a COPY of chat_ctx. If it
    were added to the live context every turn would carry every earlier
    excerpt."""
    repository = SpyRepository([chunk_row("c1")], [])
    agent, spy, _, _ = make_agent_with_knowledge(
        repository, [HealthyStream(["one "]), HealthyStream(["two "])]
    )
    ctx = user_ctx("amenities")

    await run_llm_node(agent, ctx)
    agent.finish_turn(ctx)
    second = user_ctx("and the location")
    await run_llm_node(agent, second)

    assert len(system_texts(spy.chat_ctxs[1])) == 1
    assert system_texts(ctx) == [], "the live context was mutated"


# -- scope, ranking and the cap -----------------------------------------


async def test_a_closed_chunk_never_reaches_the_prompt():
    """`admin_only` and `inventory_governed` are not prompt-eligible, and the
    gate is code, not the query alone."""
    repository = SpyRepository(
        [
            chunk_row("closed", scope="admin_only", body="Internal margin note."),
            chunk_row("open", scope="general_knowledge", body="There is a pool."),
        ],
        [],
    )
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("tell me about it"))

    (message,) = system_texts(spy.chat_ctxs[0])
    assert "Internal margin note." not in message
    assert "There is a pool." in message
    assert [c.chunk_id for c in agent._knowledge_context.chunks] == ["open"]


async def test_unbound_project_prose_is_not_eligible():
    repository = SpyRepository(
        [chunk_row("c1", scope="project_knowledge", project_id=None, body="Unbound.")],
        [],
    )
    agent, spy, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("tell me"))

    assert system_texts(spy.chat_ctxs[0]) == []


async def test_at_most_four_chunks_are_requested():
    repository = SpyRepository([chunk_row(f"c{i}") for i in range(9)], [])
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("everything"))

    assert repository.searches[0]["limit"] == 4
    assert len(agent._knowledge_context.chunks) <= 4


# -- the audit row and the event ----------------------------------------


async def test_one_buffered_knowledge_use_per_turn_records_the_withheld_match():
    """`knowledge_use` has a composite FK to `lead_turns(lead_id, turn_index)`
    and lead turns are only written at persist, so the row CANNOT exist
    during the call. The turn buffers it; persist writes it."""
    repository = SpyRepository(
        [chunk_row("c1", body="Prices start at 999,000.")],
        [
            figure_row(
                "f1",
                "c1",
                value=999000.0,
                surface="999,000",
                approved=False,
                sentence="Prices start at 999,000.",
            )
        ],
    )
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])
    ctx = user_ctx("what do studios cost")

    await run_llm_node(agent, ctx)
    await run_llm_node(agent, ctx)

    assert repository.knowledge_use == [], "nothing may be written during the call"
    (buffered,) = agent.knowledge_use()
    assert buffered.withheld_figure_match is True
    assert buffered.turn_index == agent._knowledge_context.turn_index
    assert buffered.elapsed_ms >= 0


async def test_the_query_fingerprint_is_not_the_buyer_utterance():
    """The buffered record is persisted and read by admins. docs/10- keeps
    buyer words out of it, so the fingerprint must not be the sentence."""
    repository = SpyRepository([chunk_row("c1")], [])
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("a very distinctive buyer question"))

    (buffered,) = agent.knowledge_use()
    assert "distinctive" not in buffered.query_fingerprint


async def test_persist_writes_the_buffered_rows_after_the_turns():
    """Ordering is the point: the composite FK means a knowledge_use row
    written before its lead_turn violates it."""
    from adapter.persist import LeadWriter

    order: list[str] = []

    class OrderingRepository:
        async def start_lead(self, **_):
            return "lead-1"

        async def finish_lead(self, *_, **__):
            return None

        async def set_ambassador_name(self, *_, **__):
            return None

        async def put_brief(self, *_, **__):
            return None

        async def put_contact(self, *_, **__):
            return None

        async def add_turn(self, *_, **__):
            order.append("turn")

        async def record_knowledge_use(self, *_, **__):
            order.append("knowledge_use")

    writer = LeadWriter(OrderingRepository(), _sealer())
    await writer.persist(_snapshot_with_knowledge_use())

    assert order and order[-1] == "knowledge_use"
    assert order.index("turn") < order.index("knowledge_use")


async def test_a_turn_with_no_repository_skips_retrieval_and_says_why():
    """A database that is absent, paused or not yet connected must not cost
    the turn its 250ms budget, and must not fail the call."""
    import json

    from adapter.agent import AmbassadorAgent
    from adapter.events import EventLog

    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(settings=make_settings(), log=log, knowledge=None)
    agent._llm = SpyLLM([HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("tell me about the amenities"))
    await log.aclose()

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    (skipped,) = [e for e in events if e.get("event") == "knowledge_retrieval_skipped"]
    assert skipped["reason"]


async def test_the_event_carries_counts_and_elapsed_but_no_buyer_or_document_words():
    import json

    repository = SpyRepository(
        [chunk_row("c1", body="A very distinctive brochure sentence.")], []
    )
    agent, _, log, buf = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("a very distinctive buyer question"))
    await log.aclose()

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    (retrieved,) = [e for e in events if e.get("event") == "knowledge_retrieved"]
    assert retrieved["chunks"] == 1
    assert "elapsed_ms" in retrieved
    body = json.dumps(retrieved)
    assert "distinctive" not in body


# -- the seam ------------------------------------------------------------


async def test_retrieval_does_not_run_when_the_deterministic_policy_owns_the_turn():
    """ADR-019: retrieval happens only after the deterministic policy has
    declined. A confirmation turn never opens the model, so a search there is
    latency spent on a prompt nobody builds."""
    repository = SpyRepository([chunk_row("c1")], [])
    agent, _, _, _ = make_agent_with_knowledge(repository, [HealthyStream(["ok "])])

    await run_llm_node(agent, user_ctx("My budget is 2 crore."))

    assert repository.searches == []


# -- the pool is a task, and a turn never waits for it -------------------


async def test_an_unfinished_pool_task_reads_as_no_repository_and_is_not_awaited():
    """`build_lead_writer` is started at session start and awaited at
    shutdown. A turn arriving mid-handshake must take the skip, not the
    wait - awaiting here would put the Frankfurt handshake in front of
    llm_ttft, which is the latency the task exists to avoid."""
    import asyncio

    from adapter.retrieval import repository_when_ready

    started = asyncio.Event()

    async def never_finishes():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(never_finishes())
    await started.wait()
    try:
        provider = repository_when_ready(task)
        # Would hang rather than fail if the provider awaited the task.
        assert await asyncio.wait_for(asyncio.to_thread(provider), timeout=1) is None
    finally:
        task.cancel()


async def test_a_failed_or_cancelled_pool_task_reads_as_no_repository():
    import asyncio

    from adapter.retrieval import repository_when_ready

    async def boom():
        raise RuntimeError("no route to host")

    failed = asyncio.create_task(boom())
    await asyncio.gather(failed, return_exceptions=True)
    assert repository_when_ready(failed)() is None

    async def forever():
        await asyncio.Event().wait()

    cancelled = asyncio.create_task(forever())
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    assert repository_when_ready(cancelled)() is None

    assert repository_when_ready(None)() is None


async def test_a_connected_pool_task_yields_the_writers_repository():
    import asyncio

    from adapter.retrieval import repository_when_ready

    class Writer:
        repository = "the-pool"

    async def connected():
        return Writer()

    task = asyncio.create_task(connected())
    await task
    assert repository_when_ready(task)() == "the-pool"


async def test_a_turn_whose_pool_is_not_connected_yet_skips_and_says_not_connected():
    import asyncio
    import json

    from adapter.agent import AmbassadorAgent
    from adapter.events import EventLog
    from adapter.retrieval import KnowledgeRetriever, repository_when_ready

    async def forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    buf = StringIO()
    log = EventLog("sess_test", stream=buf, verbose=False)
    agent = AmbassadorAgent(
        settings=make_settings(),
        log=log,
        knowledge=KnowledgeRetriever(repository_when_ready(task), log=log),
    )
    agent._llm = SpyLLM([HealthyStream(["ok "])])
    try:
        await run_llm_node(agent, user_ctx("tell me about the amenities"))
        await log.aclose()
    finally:
        task.cancel()

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    (skipped,) = [e for e in events if e.get("event") == "knowledge_retrieval_skipped"]
    assert skipped["reason"] == "not_connected"


async def test_an_unexpected_failure_skips_with_a_closed_code_not_a_class_name():
    """`CLEAR_EVENTS` promises `reason` is a fixed code. `type(exc).__name__`
    is an open set: a new dependency's exception class would put an
    unreviewed string on the stream that the classification never approved."""
    import json

    class Exploding:
        async def search_chunks(self, *_, **__):
            raise ZeroDivisionError("nobody planned for this")

        async def figures_for_chunks(self, *_, **__):
            return []

    agent, _, log, buf = make_agent_with_knowledge(
        Exploding(), [HealthyStream(["ok "])]
    )
    await run_llm_node(agent, user_ctx("tell me about the amenities"))
    await log.aclose()

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    (skipped,) = [e for e in events if e.get("event") == "knowledge_retrieval_skipped"]
    assert skipped["reason"] != "ZeroDivisionError"
    assert skipped["reason"] == "unknown"
