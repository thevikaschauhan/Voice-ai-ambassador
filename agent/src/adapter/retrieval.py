"""The per-turn knowledge read, and the one place it is allowed to happen.

ADR-019's prompt seam. Retrieval runs after the deterministic policy has
declined the turn and before the model opens, once per buyer turn, and the
result is frozen for that turn.

Three properties live here rather than in the agent, because each of them is
a rule about retrieval and not a rule about conversation:

- **Once.** `llm_node` runs again for the same turn on a tool split or a
  preemptive generation. The cache is keyed on turn index, and the SECOND
  call returns the identical object. Recomputing would be the bug: a second
  search can rank differently or race a revocation, and then the audit
  records one figure set while the buyer heard another.
- **Bounded.** At most 250ms is added before `llm_ttft`. A slow or paused
  database costs the turn its budget and nothing more; the turn then proceeds
  with the inventory figures it already had.
- **Closed on failure.** Every failure path returns a miss - no excerpt, no
  figure extension - and says why in a classified event. A retrieval that
  half-worked would be the dangerous case, which is why the context is built
  in one step from one set of rows rather than accumulated.

The buffered `KnowledgeUse` records live here too. They cannot be written
during the call: `knowledge_use` has a composite foreign key to
`lead_turns(lead_id, turn_index)`, and lead turns are written by
`LeadWriter.persist` after the call ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import unicodedata
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ambassador.knowledge import (
    ChunkScope,
    FigureOccurrence,
    KnowledgeContext,
    RetrievedChunk,
    build_knowledge_context,
)
from ambassador.schemas import AllowedFigures, KnowledgeUse

import yaml

from .events import EventLog
from .persist import _failure_code

# ADR-019. Not a constant anybody should tune away: it is the share of
# `llm_ttft` the buyer will not notice, and the whole reason full-text search
# was chosen over a retrieval service.
BUDGET_SECONDS = 0.25
CHUNK_LIMIT = 4


class ChunkSource(Protocol):
    """What retrieval needs from the repository, and nothing else."""

    async def search_chunks(
        self, query: str, *, project_ids: Sequence[str], limit: int
    ) -> list[dict[str, Any]]: ...

    async def figures_for_chunks(
        self, chunk_ids: Sequence[Any]
    ) -> list[dict[str, Any]]: ...


_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# What counts as a word character: any LETTER, any combining MARK, any
# NUMBER. Written as Unicode categories rather than as a character class,
# because `\w` - and `[^\W_]`, which is the same set minus underscore - does
# NOT match combining marks. The first version of this used `[^\W_]+` under a
# comment warning about precisely that, and it shredded Devanagari: the
# virama, the nukta and the matras all read as separators, so
# "अक्वाराइज़ ... हैंडओवर" tokenised to ['अक', 'इज', 'मत', 'डओवर'] while
# Postgres indexed the whole words, and a Hindi call could never match a
# Hindi document. Unpointed Arabic survived only because its letters are
# category Lo; one diacritic splits it the same way.
_WORD_CATEGORIES = frozenset("LMN")

# One character is never a content word in any of the three languages, and a
# single letter matches half the corpus. Applied in `content_tokens`, NOT in
# `tokenise`, so it cannot cost us parity with the index.
_MINIMUM_TOKEN = 2


@lru_cache(maxsize=1)
def load_stopwords(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Query-side stopwords per language, from `data/stopwords.yaml`.

    Cached because it is read on the turn path and the file cannot change
    inside a call.
    """
    source = path or _DATA_DIR / "stopwords.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return {
        language: frozenset(_fold(word) for word in words or ())
        for language, words in raw.items()
    }


def _fold(word: str) -> str:
    """The form used to COMPARE a token against the stopword list.

    NFKC because Arabic and Hindi both encode the same grapheme more than one
    way, and a list matching only one encoding silently stops working on the
    other. This is deliberately NOT what goes into the query: Postgres's
    `simple` configuration lowercases and does not normalise, so normalising
    the query token would be a second way to disagree with the index.
    """
    return unicodedata.normalize("NFKC", word).casefold()


def tokenise(utterance: str) -> list[str]:
    """The words Postgres would index, in the order they were said.

    Parity with the index side is the property this has to hold: a query
    lexeme that is not an index lexeme matches nothing, however reasonable it
    looks. Lowercased and not otherwise normalised, because that is exactly
    what `to_tsvector('simple', ...)` does.
    """
    tokens: list[str] = []
    current: list[str] = []
    for character in utterance:
        if unicodedata.category(character)[0] in _WORD_CATEGORIES:
            current.append(character)
        elif current:
            tokens.append("".join(current).lower())
            current = []
    if current:
        tokens.append("".join(current).lower())
    return tokens


def content_tokens(utterance: str, language: str) -> list[str]:
    """The words worth searching for, in the order the buyer said them.

    Postgres's `simple` configuration removes no stopwords, and
    `plainto_tsquery` joins what is left with AND. Together those turn "how
    much are the Aquarise studios and when is handover" into a query that
    requires `how` and `much` and `are` and `the` to appear in a brochure
    sentence, which is why the whole seam matched nothing on real speech.

    An unknown language keeps every token rather than raising: a call in a
    language with no list yet should retrieve noisily, not fail.
    """
    stopwords = load_stopwords().get(language, frozenset())
    seen: set[str] = set()
    tokens: list[str] = []
    for token in tokenise(utterance):
        if len(token) < _MINIMUM_TOKEN or _fold(token) in stopwords or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def fingerprint(utterance: str) -> str:
    """A stable handle for the query that is not the query.

    The buffered record is persisted and read by admins; docs/10- keeps buyer
    words out of it. Truncated because this identifies a repeat, it does not
    have to resist reversal by someone who already has the database.
    """
    return hashlib.sha256(utterance.strip().lower().encode("utf-8")).hexdigest()[:16]


def _chunk_from_row(row: dict[str, Any]) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(row["id"]),
        document_id=str(row["document_id"]),
        document_revision=int(row["document_revision"]),
        heading=row.get("heading"),
        body=row["prompt_body"] or "",
        scope=ChunkScope(
            chunk_id=str(row["id"]),
            retrieval_scope=row["retrieval_scope"],
            project_id=row.get("project_id"),
            conflict_code=row.get("conflict_code"),
        ),
    )


def _occurrence_from_row(row: dict[str, Any]) -> FigureOccurrence:
    return FigureOccurrence(
        figure_id=str(row["id"]),
        chunk_id=str(row["chunk_id"]),
        value=float(row["value"]),
        kind=row["kind"],
        currency=row.get("currency"),
        unit=row.get("unit"),
        surface=row["surface"],
        source_sentence=row["source_sentence"],
        approved=bool(row["approved"]),
    )


class KnowledgeRetriever:
    def __init__(
        self,
        source: Callable[[], ChunkSource | None],
        *,
        log: EventLog,
        language: str = "en",
        limit: int = CHUNK_LIMIT,
        budget_seconds: float = BUDGET_SECONDS,
    ) -> None:
        # A provider, not a repository: the pool is connected by a task
        # started at session start and awaited only at shutdown, so
        # whether one exists is a question with a different answer on
        # each turn. Resolved per turn, never awaited.
        self._source = source
        self._log = log
        self._language = language
        self._limit = limit
        self._budget = budget_seconds
        self._context: KnowledgeContext | None = None
        self._buffered: list[KnowledgeUse] = []

    def buffered(self) -> list[KnowledgeUse]:
        """The rows persist will write, in turn order."""
        return list(self._buffered)

    async def for_turn(
        self,
        *,
        turn_index: int,
        query: str,
        base: AllowedFigures,
        project_ids: Sequence[str],
    ) -> KnowledgeContext:
        # The whole point of the cache: same turn, same object, no second
        # search. Identity rather than equality, so a caller cannot be handed
        # a rebuilt set that merely compares equal today.
        if self._context is not None and self._context.turn_index == turn_index:
            return self._context

        started = asyncio.get_running_loop().time()
        try:
            context = await asyncio.wait_for(
                self._retrieve(turn_index, query, base, project_ids),
                timeout=self._budget,
            )
        except LookupError:
            # Not an error: a call whose pool has not finished connecting, or
            # a deployment with no database at all. Named separately from the
            # generic failure so the event says "not_connected" rather than
            # the name of an exception class.
            context = self._miss(turn_index, query, base, started, "not_connected")
        except TimeoutError:
            context = self._miss(turn_index, query, base, started, "budget_exceeded")
        except Exception as exc:  # a paused database must not end the call
            # A CLOSED code, never the class name. `CLEAR_EVENTS` promises
            # `reason` is a fixed set, and a new dependency's exception class
            # would otherwise put an unreviewed string on the stream.
            context = self._miss(turn_index, query, base, started, _failure_code(exc))
        else:
            self._log.emit(
                "knowledge_retrieved",
                turn=turn_index,
                chunks=len(context.chunks),
                figures=len(context.figure_review_ids),
                withheld=context.withheld_figure_match,
                elapsed_ms=context.elapsed_ms,
            )

        self._context = context
        self._buffer(context)
        return context

    async def _retrieve(
        self,
        turn_index: int,
        query: str,
        base: AllowedFigures,
        project_ids: Sequence[str],
    ) -> KnowledgeContext:
        repository = self._source()
        if repository is None:
            raise LookupError("no repository")
        started = asyncio.get_running_loop().time()
        rows = await repository.search_chunks(
            content_tokens(query, self._language),
            project_ids=list(project_ids),
            limit=self._limit,
        )
        chunks = [_chunk_from_row(row) for row in rows]
        occurrences: list[FigureOccurrence] = []
        if chunks:
            figure_rows = await repository.figures_for_chunks(
                [row["id"] for row in rows]
            )
            occurrences = [_occurrence_from_row(row) for row in figure_rows]
        elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        return build_knowledge_context(
            base,
            turn_index=turn_index,
            chunks=chunks,
            occurrences=occurrences,
            query_fingerprint=fingerprint(query),
            elapsed_ms=elapsed_ms,
        )

    def _miss(
        self,
        turn_index: int,
        query: str,
        base: AllowedFigures,
        started: float,
        reason: str,
    ) -> KnowledgeContext:
        """A turn that retrieved nothing, for a stated reason.

        Identical to a genuine miss on purpose: the turn speaks exactly the
        figures inventory allows. The reason is in the event, not in the
        context, because nothing downstream may behave differently for a
        timeout than for a document nobody uploaded.
        """
        elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        self._log.emit(
            "knowledge_retrieval_skipped",
            turn=turn_index,
            reason=reason,
            elapsed_ms=elapsed_ms,
        )
        return build_knowledge_context(
            base,
            turn_index=turn_index,
            chunks=[],
            occurrences=[],
            query_fingerprint=fingerprint(query),
            elapsed_ms=elapsed_ms,
        )

    def _buffer(self, context: KnowledgeContext) -> None:
        """One record per turn, written at persist.

        A turn that retrieved nothing still gets a row: 'this turn asked and
        the corpus had nothing' is a different fact from 'this turn never
        asked', and only the buffered row can tell them apart afterwards.
        """
        self._buffered.append(
            KnowledgeUse(
                turn_index=context.turn_index,
                query_fingerprint=context.query_fingerprint,
                chunk_refs=context.chunk_refs,
                figure_review_ids=list(context.figure_review_ids),
                withheld_figure_match=context.withheld_figure_match,
                elapsed_ms=context.elapsed_ms,
            )
        )


def repository_when_ready(
    task: "asyncio.Task[Any] | None",
) -> Callable[[], ChunkSource | None]:
    """The lead store's repository, but only if it is already connected.

    `build_lead_writer` runs as a task started at session start and awaited
    at shutdown, so a turn early in the call can arrive before the handshake
    to Frankfurt finishes. This NEVER awaits it: an unfinished task reads as
    no repository, the turn takes the skip event, and the buyer waits for
    nothing. Awaiting here would put the pool handshake in front of
    `llm_ttft`, which is the latency the task exists to avoid.
    """

    def provider() -> ChunkSource | None:
        if task is None or not task.done() or task.cancelled():
            return None
        if task.exception() is not None:
            return None
        writer = task.result()
        return getattr(writer, "repository", None) if writer is not None else None

    return provider
