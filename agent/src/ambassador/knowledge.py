"""Knowledge chunking, the four retrieval scopes, and the per-turn figures gate.

ADR-019 lets a brochure add reviewed descriptive prose. It does not let a
brochure become a second source of figures, and `docs/03-`'s numeric guarantee
does not weaken because somebody uploaded a PDF. This module is where that line
is drawn in code.

## The gate fails closed, and every branch of it does

`extend_allowed_figures` returns a COPY of the base set with approved,
retrieved, eligible occurrences added. Everything else returns the base
unchanged: an unapproved occurrence, a revoked one, a chunk this turn did not
retrieve, an inventory-governed chunk, unbound project prose, and an occurrence
naming a chunk nobody supplied. Those are not six checks bolted on; they are one
rule stated six ways, which is that a number may be spoken only when a reviewer
approved THIS occurrence and the model actually saw the sentence it came from.

A partial extension would be worse than no extension. The figure would be
speakable and the audit would show a chunk that did not license it, which is
precisely the shape of evidence that looks like proof and is not.

The copy matters as much as the filter. `AllowedFigures` is built once from
inventory and shared across every turn of a call, so extending it in place would
leak one turn's brochure figures into the rest of the conversation - and into
turns whose retrieval returned nothing at all.

## The four scopes are a closure, not a preference

`admin_only` is the default because it is the only default that fails closed: a
document uploaded and forgotten must not be reachable from a call.
`inventory_governed` is closed permanently, because prices, sizes, plans,
handover, status and unit types come from `data/inventory.json` and a brochure
does not get to restate them. `project_knowledge` is closed until it is BOUND to
a project that exists in inventory, since prose about a tower we do not sell is
prose nobody can check. A conflict with a structured inventory field overrides
whatever the reviewer asked for and stays closed until the inventory is
corrected through its own review.

## Chunking is deterministic because revisions are immutable

A spoken answer names the document revision it used, which only means something
if a revision means one thing. Same text and same limits therefore produce the
same chunks, headings bound chunks so one excerpt does not answer two questions,
and consecutive chunks overlap by a paragraph so a sentence split across a
boundary is still retrievable.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal, get_args

import yaml

from .schemas import AllowedFigures, FigureKind

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

RetrievalScope = Literal[
    "admin_only", "general_knowledge", "project_knowledge", "inventory_governed"
]

ConflictCode = Literal["conflicts_with_inventory", "unknown_project"]

# What a reviewer may ask for. The same four names as the scopes, because a
# review IS a request for a scope; `None` is "not reviewed yet", which is the
# default rather than a fourth action.
ScopeAction = RetrievalScope

_SCOPES: Final[tuple[RetrievalScope, ...]] = get_args(RetrievalScope)

# The only two a model may ever see. Named once here so no caller re-derives
# the rule, which is how one of them eventually gets left out.
_PROMPT_ELIGIBLE: Final[frozenset[str]] = frozenset(
    {"general_knowledge", "project_knowledge"}
)

_PARAGRAPH_BREAK: Final = "\n\n"


@dataclass(frozen=True)
class ChunkLimits:
    """Where a chunk prefers to end, where it must end, and how much it repeats.

    Target and maximum are different numbers on purpose: one number forces
    either a mid-paragraph break at exactly N characters or an unbounded chunk
    when a paragraph is longer than N.
    """

    target_chars: int
    maximum_chars: int
    overlap_paragraphs: int


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    heading: str | None
    body: str


@dataclass(frozen=True)
class ChunkScope:
    """A chunk's settled scope, after review and after the closures."""

    chunk_id: str
    retrieval_scope: RetrievalScope
    project_id: str | None = None
    conflict_code: ConflictCode | None = None


@dataclass(frozen=True)
class FigureOccurrence:
    """One extracted figure, with the chunk it came from and whether a reviewer
    has approved THIS occurrence.

    `approved` is the projection of the append-only review history, so a
    revocation reads here as `False`. The value is per occurrence rather than
    per value on purpose: two documents can both say "1,250,000" and only one of
    them can have been reviewed.
    """

    figure_id: str
    chunk_id: str
    value: float
    kind: FigureKind
    currency: str | None
    unit: str | None
    surface: str
    source_sentence: str
    approved: bool


def load_limits(path: Path | None = None) -> ChunkLimits:
    """Parse the limits, or refuse in front of whoever edited them."""
    source = path or _DATA_DIR / "knowledge.yaml"
    raw: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    document = {} if raw is None else raw
    if not isinstance(document, dict):
        raise ValueError(
            f"{source.name}: the limits must be a mapping, got "
            f"{type(document).__name__}."
        )

    values: dict[str, int] = {}
    for field in ("target_chars", "maximum_chars", "overlap_paragraphs"):
        value = document.get(field)
        # `bool` is an `int` in Python and would read as 0 or 1 characters.
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"{source.name}: {field!r} must be a whole number, got {value!r}."
            )
        values[field] = value

    for field in ("target_chars", "maximum_chars"):
        if values[field] <= 0:
            raise ValueError(
                f"{source.name}: {field!r} must be positive, got {values[field]}."
            )
    if values["overlap_paragraphs"] < 0:
        raise ValueError(
            f"{source.name}: 'overlap_paragraphs' must be zero or positive, got "
            f"{values['overlap_paragraphs']}."
        )
    if values["maximum_chars"] < values["target_chars"]:
        raise ValueError(
            f"{source.name}: 'maximum_chars' ({values['maximum_chars']}) is "
            f"below 'target_chars' ({values['target_chars']}), which collapses "
            "the two into one number and makes every chunk end at the hard "
            "limit."
        )
    return ChunkLimits(**values)


def _is_heading(paragraph: str) -> bool:
    """A markdown-style heading. Deliberately narrow: the parsers upstream emit
    `#` headings, and guessing at bold or title-case lines would make chunk
    boundaries depend on prose style rather than on structure."""
    return paragraph.startswith("#")


def _heading_text(paragraph: str) -> str:
    return paragraph.lstrip("#").strip()


def _split_oversized(paragraph: str, maximum: int) -> list[str]:
    """Break a paragraph longer than the hard maximum on whitespace.

    Split rather than dropped: a 5,000-character paragraph is an ordinary shape
    in a brochure, and losing it silently would mean a document that ingested
    "successfully" with a hole in it.
    """
    if len(paragraph) <= maximum:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for word in paragraph.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > maximum:
            pieces.append(current)
            current = word
        else:
            current = candidate
        # A single word longer than the maximum still has to fit.
        while len(current) > maximum:
            pieces.append(current[:maximum])
            current = current[maximum:]
    # Unconditional, because `current` cannot be empty here and a guard on it
    # would be a branch no test could ever take. This function is only entered
    # with a paragraph longer than the maximum, so `split()` yields at least
    # one word; and the loop above stops at `> maximum`, so it always leaves a
    # remainder of 1..maximum characters rather than consuming the string.
    pieces.append(current)
    return pieces


def chunk_text(text: str, limits: ChunkLimits) -> list[TextChunk]:
    """Heading- and paragraph-aware chunks, deterministically.

    A heading always starts a new chunk, even well below the target: two topics
    in one excerpt is how a retrieved chunk answers the wrong question.
    """
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    paragraphs: list[str] = []

    for raw in text.split(_PARAGRAPH_BREAK):
        paragraph = raw.strip()
        if not paragraph:
            continue
        if _is_heading(paragraph):
            if paragraphs:
                sections.append((heading, paragraphs))
            heading = _heading_text(paragraph)
            paragraphs = []
            continue
        paragraphs.extend(_split_oversized(paragraph, limits.maximum_chars))
    if paragraphs:
        sections.append((heading, paragraphs))

    chunks: list[TextChunk] = []
    for section_heading, section_paragraphs in sections:
        current: list[str] = []
        for paragraph in section_paragraphs:
            candidate = current + [paragraph]
            body = _PARAGRAPH_BREAK.join(candidate)
            # Close the chunk when adding this paragraph would pass the target,
            # unless nothing is in it yet - a paragraph on its own always fits
            # somewhere, because `_split_oversized` has already bounded it.
            if current and (
                len(body) > limits.maximum_chars or len(body) > limits.target_chars
            ):
                chunks.append(
                    TextChunk(
                        len(chunks), section_heading, _PARAGRAPH_BREAK.join(current)
                    )
                )
                overlap = (
                    current[-limits.overlap_paragraphs :]
                    if limits.overlap_paragraphs
                    else []
                )
                # The overlap is a retrieval convenience; the maximum is a
                # bound. When repeating the previous paragraph would push the
                # new chunk past it, the overlap is what gives way - the
                # alternative is a chunk nothing bounds, which is the failure
                # the maximum exists to prevent.
                if len(_PARAGRAPH_BREAK.join([*overlap, paragraph])) > (
                    limits.maximum_chars
                ):
                    overlap = []
                current = [*overlap, paragraph]
            else:
                current = candidate
        # Also unconditional, for the same reason. A section reaches this loop
        # only when it has at least one paragraph (sections with none are never
        # collected above), and the body of the loop always leaves the last
        # paragraph in `current` - it opens the next chunk rather than closing
        # one. So there is always a final chunk to emit.
        chunks.append(
            TextChunk(len(chunks), section_heading, _PARAGRAPH_BREAK.join(current))
        )
    return chunks


def review_scope(
    action: ScopeAction | None,
    *,
    project_id: str | None,
    inventory_project_ids: Collection[str],
    conflicts_with_inventory: bool = False,
) -> ChunkScope:
    """The scope a chunk actually gets, after the closures.

    `action` is what the reviewer asked for and `None` is "not reviewed yet".
    The closures override the request rather than validating it, because a
    conflict or an unknown project is a fact about the document and not a
    mistake the reviewer can fix by choosing differently.
    """
    if action is not None and action not in _SCOPES:
        raise ValueError(
            f"{action!r} is not a retrieval scope; expected one of "
            f"{', '.join(_SCOPES)}."
        )
    if action == "general_knowledge" and project_id is not None:
        raise ValueError(
            "general_knowledge carries no project_id: a process or FAQ chunk "
            "bound to a project would rank as context for a tower it says "
            "nothing about."
        )

    if conflicts_with_inventory:
        # Overrides everything. The answer to a brochure that disagrees with a
        # structured field is to correct the inventory through its own review,
        # not to publish both and let retrieval pick.
        return ChunkScope("", "admin_only", None, "conflicts_with_inventory")

    if action == "project_knowledge":
        if project_id is None or project_id not in inventory_project_ids:
            return ChunkScope("", "admin_only", None, "unknown_project")
        return ChunkScope("", "project_knowledge", project_id, None)

    if action is None:
        return ChunkScope("", "admin_only", None, None)
    return ChunkScope("", action, None, None)


def is_prompt_eligible(scope: ChunkScope) -> bool:
    """Whether a model may ever see this chunk.

    A conflict closes a chunk whatever its scope says, so both are checked here
    rather than trusting `retrieval_scope` alone.
    """
    return scope.conflict_code is None and scope.retrieval_scope in _PROMPT_ELIGIBLE


def extend_allowed_figures(
    base: AllowedFigures,
    *,
    occurrences: Iterable[FigureOccurrence],
    chunks: Sequence[ChunkScope],
    retrieved_chunk_ids: Collection[str],
) -> AllowedFigures:
    """A COPY of `base` with this turn's approved knowledge figures added.

    Returns `base` itself when nothing qualifies, which is the common case: a
    turn with no retrieval, or with retrieval that found only closed chunks,
    speaks exactly the figures the inventory allows.
    """
    eligible = {
        scope.chunk_id
        for scope in chunks
        if is_prompt_eligible(scope) and scope.chunk_id in retrieved_chunk_ids
    }

    amounts: set[float] = set()
    currency_amounts: set[float] = set()
    percents: set[float] = set()
    years: set[int] = set()

    for occurrence in occurrences:
        if not occurrence.approved or occurrence.chunk_id not in eligible:
            continue
        if occurrence.kind == "amount":
            amounts.add(float(occurrence.value))
            # Only a currency amount joins the set a PRICE is checked against.
            # A square footage in there would let "It starts at AED 420"
            # validate, which is the bug `currency_amounts` exists to stop.
            if occurrence.currency:
                currency_amounts.add(float(occurrence.value))
        elif occurrence.kind == "percent":
            percents.add(float(occurrence.value))
        elif occurrence.kind == "year":
            years.add(int(occurrence.value))
        # A `count` extends nothing. Counts are the guardrail's documented 0-12
        # exemption and have no set of their own, so adding them would widen
        # what may be spoken on the strength of a number nobody checked.

    if not (amounts or currency_amounts or percents or years):
        return base
    return replace(
        base,
        amounts=base.amounts | amounts,
        currency_amounts=base.currency_amounts | currency_amounts,
        percents=base.percents | percents,
        years=base.years | years,
    )
