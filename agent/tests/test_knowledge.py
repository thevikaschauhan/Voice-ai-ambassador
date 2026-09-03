"""Knowledge chunking, the four scopes, and the per-turn figures gate.

P2-S07's and P2-S09's RED tests live here. The rule the whole file defends is
ADR-019's: a brochure may add reviewed descriptive prose, and it may never
become a second source of figures. `docs/03-`'s numeric guarantee does not
weaken because a document was uploaded, so the extension is source-scoped and
fails CLOSED - a retrieval miss, an unapproved occurrence, a revoked one, a
chunk nobody retrieved, an inventory-governed fact and unbound project prose
all leave the turn's allowed set exactly as the inventory built it.

Imports sit inside the tests for the reason they did in tests/test_leads.py: at
module level this file would be one collection error instead of one failure per
case, which would make the RED commit unreadable as a specification and would
stop the gate counting failures against new cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATA = Path(__file__).resolve().parents[2] / "data"

# Small limits, injected. docs/10- is explicit that tests use injected limits
# rather than duplicating the algorithm, so nothing here restates 1600/2400.
TINY = {"target_chars": 120, "maximum_chars": 200, "overlap_paragraphs": 1}


def limits(**overrides):
    from ambassador.knowledge import ChunkLimits

    return ChunkLimits(**{**TINY, **overrides})


def write_limits(tmp_path: Path, body: dict) -> Path:
    path = tmp_path / "knowledge.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def occurrence(**overrides):
    """One extracted figure occurrence. Unapproved by default, because parsing
    never approves (docs/10- step 5)."""
    from ambassador.knowledge import FigureOccurrence

    base = dict(
        figure_id="fig-1",
        chunk_id="chunk-1",
        value=1_250_000.0,
        kind="amount",
        currency="AED",
        unit=None,
        surface="AED 1,250,000",
        source_sentence="Prices start at AED 1,250,000.",
        approved=False,
    )
    return FigureOccurrence(**{**base, **overrides})


def eligible_chunk(chunk_id="chunk-1", scope="general_knowledge", project_id=None):
    from ambassador.knowledge import ChunkScope

    return ChunkScope(
        chunk_id=chunk_id,
        retrieval_scope=scope,
        project_id=project_id,
        conflict_code=None,
    )


def empty_base():
    from ambassador.schemas import AllowedFigures

    return AllowedFigures(
        amounts=frozenset(),
        percents=frozenset(),
        years=frozenset(),
        currency_amounts=frozenset(),
    )


# --- P2-S07's named RED test ----------------------------------------------


def test_chunks_default_closed_and_inventory_governed_facts_and_unbound_project_prose_never_enter_prompt_context():
    """The four scopes, and which of them a model may ever see.

    A chunk is `admin_only` until a reviewer says otherwise, which is the only
    default that fails closed: a document uploaded and forgotten must not be
    reachable from a call. `inventory_governed` is closed permanently - prices,
    sizes, plans and handover come from data/inventory.json and a brochure does
    not get to restate them. `project_knowledge` is closed until it is BOUND to
    a project that exists in inventory, because prose about a tower we do not
    sell is prose we cannot check.
    """
    from ambassador.knowledge import is_prompt_eligible, review_scope

    inventory = ["binghatti-skyrise"]

    default = review_scope(None, project_id=None, inventory_project_ids=inventory)
    assert default.retrieval_scope == "admin_only"
    assert not is_prompt_eligible(default)

    governed = review_scope(
        "inventory_governed", project_id=None, inventory_project_ids=inventory
    )
    assert governed.retrieval_scope == "inventory_governed"
    assert not is_prompt_eligible(governed)

    unbound = review_scope(
        "project_knowledge", project_id=None, inventory_project_ids=inventory
    )
    assert unbound.retrieval_scope == "admin_only"
    assert unbound.conflict_code == "unknown_project"
    assert not is_prompt_eligible(unbound)

    unknown = review_scope(
        "project_knowledge",
        project_id="binghatti-atlantis",
        inventory_project_ids=inventory,
    )
    assert unknown.retrieval_scope == "admin_only"
    assert unknown.conflict_code == "unknown_project"
    assert not is_prompt_eligible(unknown)

    # The two that a model may see, and only these two.
    general = review_scope(
        "general_knowledge", project_id=None, inventory_project_ids=inventory
    )
    bound = review_scope(
        "project_knowledge",
        project_id="binghatti-skyrise",
        inventory_project_ids=inventory,
    )
    assert is_prompt_eligible(general)
    assert is_prompt_eligible(bound)
    assert bound.project_id == "binghatti-skyrise"


def test_prose_that_conflicts_with_inventory_stays_closed_whatever_was_asked_for():
    """A conflict is not a scope the reviewer chooses between; it overrides the
    choice. A brochure saying a tower hands over in 2027 when inventory says
    2026 is exactly the case the numeric guarantee exists for, and the answer
    is to fix the inventory through its own review, not to publish both."""
    from ambassador.knowledge import is_prompt_eligible, review_scope

    for action in ("general_knowledge", "project_knowledge", "inventory_governed"):
        # Only project_knowledge may name a project; the test asserting that is
        # one case above, and passing an id here for the others would fail on
        # that rule rather than on the conflict this case is about.
        decision = review_scope(
            action,
            project_id="binghatti-skyrise" if action == "project_knowledge" else None,
            inventory_project_ids=["binghatti-skyrise"],
            conflicts_with_inventory=True,
        )
        assert decision.retrieval_scope == "admin_only", action
        assert decision.conflict_code == "conflicts_with_inventory"
        assert not is_prompt_eligible(decision)


def test_general_knowledge_may_not_carry_a_project_id():
    """docs/02-: for `general_knowledge`, `project_id` is null. A process or FAQ
    chunk bound to a project would rank as project context for a tower it says
    nothing about."""
    from ambassador.knowledge import review_scope

    with pytest.raises(ValueError, match="general_knowledge"):
        review_scope(
            "general_knowledge",
            project_id="binghatti-skyrise",
            inventory_project_ids=["binghatti-skyrise"],
        )


def test_an_unknown_scope_action_is_refused():
    from ambassador.knowledge import review_scope

    with pytest.raises(ValueError, match="prompt_eligible"):
        review_scope("prompt_eligible", project_id=None, inventory_project_ids=["x"])


# --- P2-S09's named RED test, and the dispatch's ---------------------------


def test_only_approved_figures_from_retrieved_chunks_extend_the_turn_set():
    """The positive case, stated narrowly so the negative ones mean something.

    An approved occurrence in a retrieved, eligible chunk joins a COPY of the
    base set for this turn. It joins by kind, and an amount with a currency
    also joins `currency_amounts`, because that is the set a PRICE is checked
    against - an amount added without it would be speakable as a bare number
    and blocked the moment it appeared beside "AED", which is the one form a
    brochure price actually takes.
    """
    from ambassador.knowledge import extend_allowed_figures

    base = empty_base()
    extended = extend_allowed_figures(
        base,
        occurrences=[occurrence(approved=True)],
        chunks=[eligible_chunk()],
        retrieved_chunk_ids=["chunk-1"],
    )

    assert 1_250_000.0 in extended.amounts
    assert 1_250_000.0 in extended.currency_amounts
    # A COPY: the base set the inventory built is untouched, because it is
    # shared across every turn of the call.
    assert extended is not base
    assert base.amounts == frozenset()
    assert base.currency_amounts == frozenset()


def test_revoked_unretrieved_or_inventory_governed_facts_and_unbound_project_prose_never_extend_allowed_figures():
    """Every way the extension must fail closed, in one place.

    Each of these is a plausible-looking route to speaking a number nobody
    approved, and the reason they are one test is that they share a single
    consequence: the turn's set is byte-identical to the base. A partial
    extension would be worse than none, because the figure would be speakable
    and the audit would show a chunk that did not license it.
    """
    from ambassador.knowledge import extend_allowed_figures

    base = empty_base()

    def extended(**kwargs):
        return extend_allowed_figures(base, **kwargs)

    # 1. Never approved. Parsing extracts; it does not approve.
    assert (
        extended(
            occurrences=[occurrence(approved=False)],
            chunks=[eligible_chunk()],
            retrieved_chunk_ids=["chunk-1"],
        )
        == base
    )

    # 2. Approved once, since revoked. `approved` is the projection of the
    #    append-only review history, so a revocation reads as False here.
    assert (
        extended(
            occurrences=[occurrence(approved=False, figure_id="fig-revoked")],
            chunks=[eligible_chunk()],
            retrieved_chunk_ids=["chunk-1"],
        )
        == base
    )

    # 3. Approved, eligible, but this turn did not retrieve it. A retrieval
    #    miss fails closed: the model never saw the sentence, so the number
    #    must not be speakable.
    assert (
        extended(
            occurrences=[occurrence(approved=True)],
            chunks=[eligible_chunk()],
            retrieved_chunk_ids=[],
        )
        == base
    )

    # 4. Approved and retrieved, but the chunk is inventory-governed.
    #    Approving a figure never turns a governed chunk into prompt material.
    assert (
        extended(
            occurrences=[occurrence(approved=True)],
            chunks=[eligible_chunk(scope="inventory_governed")],
            retrieved_chunk_ids=["chunk-1"],
        )
        == base
    )

    # 5. Approved and retrieved, but the project prose was never bound.
    assert (
        extended(
            occurrences=[occurrence(approved=True)],
            chunks=[eligible_chunk(scope="admin_only")],
            retrieved_chunk_ids=["chunk-1"],
        )
        == base
    )

    # 6. Approved and retrieved, but the occurrence names a chunk nobody
    #    supplied. An occurrence whose chunk is unknown cannot be licensed by
    #    a chunk that happens to be eligible.
    assert (
        extended(
            occurrences=[occurrence(approved=True, chunk_id="chunk-elsewhere")],
            chunks=[eligible_chunk()],
            retrieved_chunk_ids=["chunk-1", "chunk-elsewhere"],
        )
        == base
    )


def test_an_approved_figure_in_one_chunk_does_not_license_the_same_value_in_another():
    """Provenance is per occurrence, not per value. Two documents can both say
    "1,250,000" and only one of them can have been reviewed."""
    from ambassador.knowledge import extend_allowed_figures

    base = empty_base()
    extended = extend_allowed_figures(
        base,
        occurrences=[
            occurrence(figure_id="fig-ok", chunk_id="chunk-1", approved=True),
            occurrence(
                figure_id="fig-no",
                chunk_id="chunk-2",
                approved=False,
                value=9_999_999.0,
                surface="AED 9,999,999",
            ),
        ],
        chunks=[eligible_chunk("chunk-1"), eligible_chunk("chunk-2")],
        retrieved_chunk_ids=["chunk-1", "chunk-2"],
    )
    assert 1_250_000.0 in extended.amounts
    assert 9_999_999.0 not in extended.amounts


@pytest.mark.parametrize(
    ("kind", "value", "field"),
    [
        ("amount", 750_000.0, "amounts"),
        ("percent", 20.0, "percents"),
        ("year", 2027, "years"),
    ],
)
def test_each_figure_kind_joins_its_own_set(kind, value, field):
    from ambassador.knowledge import extend_allowed_figures

    extended = extend_allowed_figures(
        empty_base(),
        occurrences=[occurrence(approved=True, kind=kind, value=value, currency=None)],
        chunks=[eligible_chunk()],
        retrieved_chunk_ids=["chunk-1"],
    )
    assert value in getattr(extended, field)


def test_an_amount_without_a_currency_does_not_join_the_currency_set():
    """A square footage is an amount and is not money. `currency_amounts` is
    what a PRICE is checked against, and putting a size in it would let
    "It starts at AED 420" validate - the exact bug that set exists to stop."""
    from ambassador.knowledge import extend_allowed_figures

    extended = extend_allowed_figures(
        empty_base(),
        occurrences=[
            occurrence(approved=True, value=420.0, currency=None, unit="sqft")
        ],
        chunks=[eligible_chunk()],
        retrieved_chunk_ids=["chunk-1"],
    )
    assert 420.0 in extended.amounts
    assert 420.0 not in extended.currency_amounts


def test_a_count_occurrence_extends_nothing():
    """Counts are the guardrail's documented 0-12 exemption and have no set of
    their own. Adding them to `amounts` would widen what may be spoken on the
    strength of a number that was never checked, which is the wrong direction
    for a gate whose whole job is to fail closed."""
    from ambassador.knowledge import extend_allowed_figures

    base = empty_base()
    assert (
        extend_allowed_figures(
            base,
            occurrences=[
                occurrence(approved=True, kind="count", value=2.0, currency=None)
            ],
            chunks=[eligible_chunk()],
            retrieved_chunk_ids=["chunk-1"],
        )
        == base
    )


def test_the_base_sets_survive_the_extension():
    """The inventory figures are still allowed afterwards. An extension that
    replaced rather than added would block every price in data/inventory.json
    for the rest of the turn."""
    from ambassador.knowledge import extend_allowed_figures
    from ambassador.schemas import AllowedFigures

    base = AllowedFigures(
        amounts=frozenset({985_000.0}),
        percents=frozenset({20.0}),
        years=frozenset({2026}),
        currency_amounts=frozenset({985_000.0}),
    )
    extended = extend_allowed_figures(
        base,
        occurrences=[occurrence(approved=True)],
        chunks=[eligible_chunk()],
        retrieved_chunk_ids=["chunk-1"],
    )
    assert {985_000.0, 1_250_000.0} <= extended.amounts
    assert 20.0 in extended.percents
    assert 2026 in extended.years


# --- deterministic chunking -----------------------------------------------


def test_chunking_is_deterministic():
    """Same text, same limits, same chunks. A spoken answer names the revision
    it used, and that is only meaningful if the revision means one thing."""
    from ambassador.knowledge import chunk_text

    text = "# One\n\n" + ("alpha beta gamma. " * 20) + "\n\n" + ("delta. " * 20)
    assert chunk_text(text, limits()) == chunk_text(text, limits())


def test_no_chunk_exceeds_the_hard_maximum():
    from ambassador.knowledge import chunk_text

    text = "\n\n".join("sentence number %d. " % n * 12 for n in range(8))
    for chunk in chunk_text(text, limits()):
        assert len(chunk.body) <= TINY["maximum_chars"], chunk.body


def test_a_heading_is_carried_onto_the_chunks_beneath_it():
    """The heading is what makes an excerpt readable out of context, and the
    admin review list unreadable without."""
    from ambassador.knowledge import chunk_text

    text = "# Payment process\n\n" + ("How payments work. " * 12)
    chunks = chunk_text(text, limits())
    assert chunks
    assert all(chunk.heading == "Payment process" for chunk in chunks)


def test_a_new_heading_starts_a_new_chunk_even_mid_target():
    """A heading is a boundary in the document, so it is a boundary here. Two
    topics in one excerpt is how a retrieved chunk answers the wrong
    question."""
    from ambassador.knowledge import chunk_text

    text = "# One\n\nShort body.\n\n# Two\n\nAnother short body."
    chunks = chunk_text(text, limits())
    assert [c.heading for c in chunks] == ["One", "Two"]
    assert "Another" not in chunks[0].body


# Short enough that a repeated paragraph plus a new one still fits under the
# hard maximum. That is the ordinary case at the shipped 1600/2400; the case
# where it does NOT fit has its own test below.
SMALL_PARAGRAPHS = [f"Paragraph {n} " + "word " * 8 for n in range(6)]


def test_consecutive_chunks_overlap_by_one_paragraph():
    """docs/10-: one-paragraph overlap. A sentence split across a chunk
    boundary is a sentence full-text search cannot find, and the overlap is
    what keeps the seam retrievable."""
    from ambassador.knowledge import chunk_text

    chunks = chunk_text("\n\n".join(SMALL_PARAGRAPHS), limits())
    assert len(chunks) >= 2
    for earlier, later in zip(chunks, chunks[1:]):
        tail = earlier.body.split("\n\n")[-1]
        assert later.body.startswith(tail), (earlier.body, later.body)


def test_the_overlap_gives_way_to_the_hard_maximum():
    """The overlap is a retrieval convenience; the maximum is a bound.

    With paragraphs long enough that repeating one would push the next chunk
    past the maximum, the overlap is dropped rather than the limit breached -
    the alternative is a chunk nothing bounds, which is the failure the maximum
    exists to prevent. Found by the maximum test failing once the overlap was
    implemented, not by reading the code.
    """
    from ambassador.knowledge import chunk_text

    long_paragraphs = [f"Paragraph {n} " + "word " * 20 for n in range(4)]
    chunks = chunk_text("\n\n".join(long_paragraphs), limits())
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.body) <= TINY["maximum_chars"]
    for earlier, later in zip(chunks, chunks[1:]):
        assert not later.body.startswith(earlier.body.split("\n\n")[-1])


def test_overlap_can_be_switched_off():
    from ambassador.knowledge import chunk_text

    chunks = chunk_text("\n\n".join(SMALL_PARAGRAPHS), limits(overlap_paragraphs=0))
    assert len(chunks) >= 2
    for earlier, later in zip(chunks, chunks[1:]):
        assert not later.body.startswith(earlier.body.split("\n\n")[-1])


def test_a_paragraph_longer_than_the_maximum_is_split_rather_than_dropped():
    """A single 5,000-character paragraph is a real shape in a brochure. It
    must not silently vanish, and it must not produce a chunk over the hard
    maximum."""
    from ambassador.knowledge import chunk_text

    text = "word " * 200
    chunks = chunk_text(text, limits())
    assert chunks
    assert all(len(c.body) <= TINY["maximum_chars"] for c in chunks)
    assert "word" in chunks[0].body


def test_empty_or_whitespace_text_produces_no_chunks():
    """A parse that produced nothing must not become one empty chunk that
    retrieval can match."""
    from ambassador.knowledge import chunk_text

    for text in ("", "   \n\n  \n"):
        assert chunk_text(text, limits()) == []


def test_ordinals_are_contiguous_from_zero():
    from ambassador.knowledge import chunk_text

    text = "\n\n".join(f"Paragraph {n} " + "word " * 20 for n in range(5))
    chunks = chunk_text(text, limits())
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


# --- the limits file ------------------------------------------------------


def test_the_shipped_limits_are_the_documented_ones():
    """docs/10- names 1,600 target and 2,400 maximum with one-paragraph
    overlap. Written here as literals so a silent edit fails rather than
    rechunking every future document."""
    from ambassador.knowledge import load_limits

    loaded = load_limits()
    assert (DATA / "knowledge.yaml").exists()
    assert loaded.target_chars == 1600
    assert loaded.maximum_chars == 2400
    assert loaded.overlap_paragraphs == 1


def test_a_maximum_below_the_target_is_refused(tmp_path):
    """It would make every chunk hit the hard limit, which is the same as
    having no target at all."""
    from ambassador.knowledge import load_limits

    with pytest.raises(ValueError, match="maximum_chars"):
        load_limits(write_limits(tmp_path, {**TINY, "maximum_chars": 100}))


@pytest.mark.parametrize("field", ["target_chars", "maximum_chars"])
def test_a_non_positive_size_limit_is_refused(tmp_path, field):
    from ambassador.knowledge import load_limits

    with pytest.raises(ValueError, match=field):
        load_limits(write_limits(tmp_path, {**TINY, field: 0}))


def test_a_negative_overlap_is_refused(tmp_path):
    from ambassador.knowledge import load_limits

    with pytest.raises(ValueError, match="overlap_paragraphs"):
        load_limits(write_limits(tmp_path, {**TINY, "overlap_paragraphs": -1}))


def test_a_missing_limit_is_refused(tmp_path):
    from ambassador.knowledge import load_limits

    body = dict(TINY)
    del body["target_chars"]
    with pytest.raises(ValueError, match="target_chars"):
        load_limits(write_limits(tmp_path, body))


# --- edges the 100% gate requires covered ---------------------------------


@pytest.mark.parametrize("body", ["- a list\n", "just text\n", "1600\n"])
def test_a_limits_file_that_is_not_a_mapping_is_refused(tmp_path, body):
    from ambassador.knowledge import load_limits

    path = tmp_path / "knowledge.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_limits(path)


def test_a_single_word_longer_than_the_maximum_is_still_bounded():
    """No whitespace to split on. A URL, a table row run together by a parser,
    or a language this splitter does not tokenise all arrive as one long token,
    and the hard maximum has to hold anyway - a chunk nothing bounds is the one
    outcome the maximum exists to prevent."""
    from ambassador.knowledge import chunk_text

    word = "x" * 700
    chunks = chunk_text(word, limits())
    assert chunks
    for chunk in chunks:
        assert len(chunk.body) <= TINY["maximum_chars"]
    # Nothing is dropped: the pieces reassemble to the original.
    assert "".join(c.body for c in chunks).replace("\n\n", "") == word


def test_a_heading_with_no_body_produces_no_chunk():
    """A section heading followed by another heading is ordinary in a brochure
    table of contents. It must not become an empty chunk that retrieval can
    match, and it must not swallow the section after it."""
    from ambassador.knowledge import chunk_text

    chunks = chunk_text("# Contents\n\n# Payment process\n\nHow it works.", limits())
    assert [c.heading for c in chunks] == ["Payment process"]
    assert chunks[0].body == "How it works."


def test_text_with_no_heading_at_all_still_chunks():
    """Pasted text usually has no headings. The heading is null rather than
    invented."""
    from ambassador.knowledge import chunk_text

    chunks = chunk_text("A paragraph of pasted text.", limits())
    assert [c.heading for c in chunks] == [None]
