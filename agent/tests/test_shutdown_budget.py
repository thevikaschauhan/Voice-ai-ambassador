"""The shutdown chain has to fit the budget that kills it.

livekit-agents 1.7.0 gathers shutdown callbacks with `asyncio.gather` and NO
per-callback timeout, then force-closes the job process at
`WorkerOptions.shutdown_process_timeout`. An overrun therefore does not slow
the shutdown - it LOSES the rest of it: the provider closes, `session_end` and
the audit seal.

Today's chain can exceed it on paper several times over: the brief drain alone
defaults to 10s, the connect await was unbounded, persist runs many queries
against a 10s command timeout, and the analysis has 4s of its own.

Persist and analysis are RETRYABLE states. A lost seal is not. So every case
here asserts the same thing in the end: `session_end` was written.

Imports inside each test so RED reads N failed = N cases.
"""

from __future__ import annotations

import asyncio
import time
from io import StringIO

import pytest

pytest.importorskip("livekit.agents", reason="voice dependency group not installed")


def test_the_process_budget_is_ours_and_not_a_framework_default() -> None:
    """`shutdown_process_timeout` is absent from the CURRENT public docs (the
    server-options page documents `drain_timeout` and not this), so its value
    is whatever the pinned version happens to carry - 10.0 in 1.7.0. An
    undocumented default that decides whether our audit seal survives is not
    something to inherit silently, so we set it and say why."""
    from adapter.agent import SHUTDOWN_PROCESS_TIMEOUT, worker_options

    options = worker_options()
    assert options.shutdown_process_timeout == SHUTDOWN_PROCESS_TIMEOUT
    # Every stage budget has to fit inside it with room for the seal.
    from adapter.agent import LEAD_SECTION_BUDGET_SECONDS

    assert LEAD_SECTION_BUDGET_SECONDS < SHUTDOWN_PROCESS_TIMEOUT


async def test_the_worst_case_chain_still_seals_inside_the_budget() -> None:
    """The case the card asks for: drain, persist and analysis each take their
    maximum and the seal still happens, inside the configured budget."""
    from adapter.agent import SHUTDOWN_PROCESS_TIMEOUT, shutdown_session

    agent, log, buf, _ = _agent()
    writer = _SlowWriter(persist_seconds=30.0)
    agent.brief_extractor = _SlowDrain(30.0)  # type: ignore[assignment]

    async def slow_ask(prompt: str, *, repair: bool = False) -> str:
        await asyncio.sleep(30.0)
        return "{}"

    started = time.monotonic()
    await shutdown_session(
        agent=agent, log=log, llm=_llm(), stt_node=None,
        lead_writer=writer, ask=slow_ask,
    )
    elapsed = time.monotonic() - started
    await log.aclose()

    assert "session_end" in buf.getvalue(), "the seal is the one thing that must survive"
    assert elapsed < SHUTDOWN_PROCESS_TIMEOUT, (
        f"the chain took {elapsed:.1f}s against a {SHUTDOWN_PROCESS_TIMEOUT}s "
        "process budget, so the framework would have killed it mid-shutdown"
    )


async def test_a_drain_that_never_finishes_does_not_eat_the_whole_budget() -> None:
    """The largest single claimant: `drain(timeout=10.0)` by default, which is
    the entire 1.7.0 budget on its own. Capped at the call site rather than by
    changing the default, because the default is right for a caller who has
    time and wrong for this one."""
    from adapter.agent import BRIEF_DRAIN_BUDGET_SECONDS, shutdown_session

    agent, log, buf, _ = _agent()
    drain = _SlowDrain(30.0)
    agent.brief_extractor = drain  # type: ignore[assignment]

    started = time.monotonic()
    await shutdown_session(agent=agent, log=log, llm=_llm(), stt_node=None)
    elapsed = time.monotonic() - started
    await log.aclose()

    assert drain.timeout_seen == BRIEF_DRAIN_BUDGET_SECONDS
    assert elapsed < BRIEF_DRAIN_BUDGET_SECONDS + 2.0
    assert "session_end" in buf.getvalue()


async def test_each_overrun_is_reported_with_a_stage_and_a_timeout_code() -> None:
    """A budget that silently drops work is worse than one that overruns: the
    lead is missing either way and nothing says which stage ran out."""
    from adapter.agent import shutdown_session

    agent, log, buf, _ = _agent()
    writer = _SlowWriter(persist_seconds=30.0)

    await shutdown_session(
        agent=agent, log=log, llm=_llm(), stt_node=None, lead_writer=writer
    )
    await log.aclose()

    written = buf.getvalue()
    assert '"event": "lead_persist_failed"' in written
    assert '"code": "timeout"' in written
    assert "session_end" in written


async def test_a_stage_that_finishes_early_leaves_the_budget_for_the_next() -> None:
    """Fixed per-stage caps would have made a fast persist and a slow analysis
    fail as often as the reverse. The stages share one deadline, so unused time
    is inherited rather than wasted."""
    from adapter.agent import LEAD_SECTION_BUDGET_SECONDS, remaining_budget

    deadline = time.monotonic() + LEAD_SECTION_BUDGET_SECONDS
    generous = remaining_budget(deadline, cap=99.0)
    assert 0 < generous <= LEAD_SECTION_BUDGET_SECONDS
    # A cap tighter than the remaining time still wins.
    assert remaining_budget(deadline, cap=1.0) == 1.0
    # And an exhausted deadline yields nothing rather than a negative timeout.
    assert remaining_budget(time.monotonic() - 5.0, cap=99.0) == 0.0


# --- doubles --------------------------------------------------------------


class _SlowDrain:
    """A brief extractor whose drain honours the timeout it is given."""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self.timeout_seen: float | None = None

    async def drain(self, timeout: float = 10.0) -> None:
        self.timeout_seen = timeout
        await asyncio.sleep(min(self._seconds, timeout))

    async def aclose(self) -> None:
        return None

    @property
    def last_good(self):
        return None


class _SlowWriter:
    def __init__(self, persist_seconds: float) -> None:
        self._seconds = persist_seconds
        self.repository = None

    async def persist_or_report(self, snapshot, *, log):
        await asyncio.sleep(self._seconds)
        return "never-returned"

    async def close(self) -> None:
        return None


def _agent():
    from test_agent import HealthyStream, make_agent

    return make_agent([HealthyStream(["A studio is AED 985,000. "])])


def _llm():
    class _Built:
        async def aclose(self) -> None:
            return None

    return _Built()
