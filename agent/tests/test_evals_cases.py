"""The case set itself: does it load, and does it cover what docs/05- demands.

A harness whose coverage silently shrinks is worse than none, because the pass
rate stays green while the matrix empties. So the table in docs/05- is checked
against `categories.yaml`, and `categories.yaml` is checked against the cases
actually on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.cases import (
    CASES_DIR,
    AssertionBase,
    CaseFile,
    EvalCase,
    load_cases,
    load_categories,
)

DOCS = Path(__file__).resolve().parents[2] / "docs" / "05-evals.md"


@pytest.fixture(scope="module")
def categories():
    return load_categories()


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_every_case_loads_and_ids_are_unique(cases):
    assert cases, "no eval cases found"
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_every_case_belongs_to_a_declared_category(cases, categories):
    known = set(categories.by_key())
    unknown = sorted({c.category for c in cases} - known)
    assert not unknown, (
        f"cases in categories that categories.yaml does not declare: {unknown}"
    )


def test_every_scored_category_meets_its_minimum_coverage(cases, categories):
    """docs/05-'s "Cases" column is a floor, not a suggestion."""
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    short = [
        f"{spec.key}: {counts.get(spec.key, 0)} of {spec.min_cases}"
        for spec in categories.categories
        if spec.gate != "human" and counts.get(spec.key, 0) < spec.min_cases
    ]
    assert not short, f"categories below docs/05- minimum coverage: {short}"


def test_every_gated_category_in_the_docs_table_is_in_the_harness(categories):
    """The doc gates ten categories at 100% plus the attached-currency row it
    gained after the live bypass. A category the doc gates and the harness does
    not know about is the gap issue #6 was opened for, so the two are compared
    rather than trusted."""
    table = DOCS.read_text(encoding="utf-8")
    gated_rows = [
        row.strip()
        for row in table.splitlines()
        if row.startswith("|") and "| gate |" in row
    ]
    assert len(gated_rows) >= 11, (
        "docs/05-'s table no longer parses into gated rows; if the table shape "
        "changed, this check has to change with it rather than be deleted"
    )
    harness_gated = {spec.key for spec in categories.categories if spec.gate == "gate"}
    assert len(harness_gated) == len(gated_rows), (
        f"docs/05- gates {len(gated_rows)} categories, categories.yaml gates "
        f"{len(harness_gated)}: {sorted(harness_gated)}"
    )


def test_human_categories_are_declared_but_carry_no_automated_cases(cases, categories):
    """Verbalisation, pronunciation and barge-in are checked by ear or by a
    native speaker. They must appear in the table so the report lists them, and
    must NOT acquire machine cases that would score them silently."""
    human = {spec.key for spec in categories.categories if spec.gate == "human"}
    assert human == {"verbalisation_tables", "pronunciation_lexicon", "barge_in_audit"}
    assert not [c for c in cases if c.category in human]


def test_every_assertion_kind_can_be_evaluated(cases):
    """The base `evaluate` raises, so a kind added without one fails here rather
    than counting as a silent pass."""
    for case in cases:
        for assertion in case.assertions:
            assert type(assertion).evaluate is not AssertionBase.evaluate, (
                f"{case.id}: assertion {assertion.kind} has no evaluate()"
            )
            assert assertion.describe()


def test_must_confirm_is_refused_outside_english():
    """The deterministic policy only runs where confirmation copy exists, and
    data/confirmations.yaml is unauthored for ar and hi. A case asserting the
    confirmation there would assert a behaviour the build does not have."""
    with pytest.raises(ValidationError, match="must_confirm"):
        EvalCase.model_validate(
            {
                "id": "x",
                "category": "confirmation_policy",
                "language": "ar",
                "turns": [{"buyer": "ميزانيتي مليون"}],
                "assertions": [{"kind": "must_confirm"}],
            }
        )


def test_a_case_in_the_wrong_file_is_refused():
    with pytest.raises(ValidationError, match="different"):
        CaseFile.model_validate(
            {
                "category": "grounding_happy_path",
                "cases": [
                    {
                        "id": "x",
                        "category": "branded_pricing",
                        "language": "en",
                        "turns": [{"buyer": "hello"}],
                        "assertions": [{"kind": "must_not_escalate"}],
                    }
                ],
            }
        )


def test_no_fixture_invents_an_inventory_record():
    """AGENTS.md: tests use data/inventory.json records or fixtures, never new
    entries in the production file. A fixture naming a project that IS in
    inventory must spell it as inventory spells it, or an assertion about a real
    project is silently checking a typo.

    Fixtures deliberately name projects that do NOT exist - that is the
    unknown-project category - so this checks the opposite direction: every
    `must_reference_project` name has to be a real record.
    """
    from ambassador.inventory import load_inventory

    names = {p.name.lower() for p in load_inventory()}
    for case in load_cases():
        for assertion in case.assertions:
            if assertion.kind != "must_reference_project":
                continue
            assert any(assertion.name.lower() in name for name in names), (
                f"{case.id} asserts a project name that is not in inventory: "
                f"{assertion.name!r}"
            )


def test_no_fixture_carries_guaranteed_return_language_as_product_copy():
    """AGENTS.md forbids guaranteed-return language in prompts, UI copy, sample
    content or fixtures. The guarantee-pressure fixtures are the deliberate
    exception - their whole purpose is to be blocked - so they are exempted BY
    NAME rather than by pattern, and any other file acquiring the wording fails.
    """
    banned = re.compile(r"guaranteed|assured\s+return|risk[-\s]?free", re.IGNORECASE)
    allowed_files = {"guarantee_pressure.yaml"}
    for source in sorted((CASES_DIR / "cases").glob("*.yaml")):
        if source.name in allowed_files:
            continue
        found = banned.search(source.read_text(encoding="utf-8"))
        assert not found, f"{source.name} carries {found.group(0)!r}"
