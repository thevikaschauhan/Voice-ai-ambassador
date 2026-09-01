import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ambassador.inventory import (
    build_allowed_figures,
    derive,
    load_inventory,
    serialise_for_prompt,
)
from ambassador.schemas import Project


def test_seed_inventory_loads(projects):
    assert len(projects) >= 4
    assert any(p.status == "branded_enquiry" for p in projects)


def test_derived_figures_are_computed_not_authored(projects):
    skyrise = next(p for p in projects if p.id == "binghatti-skyrise")
    derived = derive(skyrise)
    # 985,000 at 20/50/30
    assert derived.milestone_amounts_aed == [197000, 492500, 295500]


def test_branded_project_derives_nothing(projects):
    branded = next(p for p in projects if p.status == "branded_enquiry")
    assert branded.price_from_aed is None
    assert derive(branded) is None


def test_allowed_set_contains_source_derived_and_whitelist(allowed):
    assert 985000.0 in allowed.amounts          # source price
    assert 197000.0 in allowed.amounts          # computed milestone
    assert 2000000.0 in allowed.amounts         # whitelist (visa threshold)
    assert 20.0 in allowed.percents             # plan percentage
    assert 2026 in allowed.years                # handover year
    # An invented figure is not allowed
    assert 800000.0 not in allowed.amounts


def test_branded_project_may_not_carry_a_price():
    with pytest.raises(ValidationError, match="branded_enquiry"):
        Project.model_validate(
            {
                "id": "x",
                "name": "X",
                "area": "Y",
                "status": "branded_enquiry",
                "price_from_aed": 1,
                "unit_types": ["2br"],
                "source_ref": "test",
            }
        )


def test_payment_plan_must_sum_to_100():
    with pytest.raises(ValidationError, match="sum"):
        Project.model_validate(
            {
                "id": "x",
                "name": "X",
                "area": "Y",
                "status": "selling",
                "price_from_aed": 100,
                "unit_types": ["2br"],
                "payment_plan": [
                    {"label": "booking", "pct": 20},
                    {"label": "handover", "pct": 70},
                ],
                "source_ref": "test",
            }
        )


def test_prompt_serialisation_inlines_derived_figures(projects):
    block = serialise_for_prompt(projects)
    assert "AED 197,000" in block                       # computed, in the prompt
    assert "price on enquiry only" in block             # branded rule inline
    assert "never state a figure" in block


def test_the_whitelist_identifier_is_classified_apart_from_the_quantities(allowed):
    """`identifiers` is the mirror of `currency_amounts`: both are subsets of
    `amounts` that the guardrail is right to ignore and verbalisation is not.

    The distinction is which way the digit fallback is wrong. For a square
    footage it is right and an authored form would be the defect; for the
    hotline it is the defect - "eighty thousand and fifteen", on the escalation
    path. Lumping them together is what kept the hotline out of the reviewer
    packet.
    """
    assert 80015.0 in allowed.identifiers
    assert 80015.0 in allowed.amounts  # still speakable, still guardrailed
    assert 80015.0 not in allowed.currency_amounts
    assert not (allowed.identifiers & allowed.currency_amounts)
    # Sizes are quantities, not identifiers, and inventory contributes none.
    assert 420.0 in allowed.amounts and 420.0 not in allowed.identifiers


# --- the loader guards, and the record that exercises none of the optional
# fields. AGENTS.md asks for 100% branch coverage on derivation code and this is
# derivation code; until now every one of these branches was unmeasured. Each
# guard is reproduced with a real file, because a monkeypatched loader proves
# the guard runs and not that a malformed file reaches it.


def write_inventory(tmp_path, entries: list[dict]) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def write_whitelist(tmp_path, body: dict) -> Path:
    path = tmp_path / "whitelist.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def a_project(**overrides) -> dict:
    """The minimum a Project needs, so a test can say only what it is about."""
    return {
        "id": "p",
        "name": "P",
        "area": "A",
        "status": "selling",
        "unit_types": ["studio"],
        "source_ref": "test fixture",
    } | overrides


def test_duplicate_project_ids_are_rejected(tmp_path):
    """Ids are how every other module refers to a project, so two records
    sharing one is not a duplicate row, it is an ambiguous reference: the name
    read back in a confirmation and the price checked against the allowed set
    could come from different records."""
    path = write_inventory(
        tmp_path, [a_project(name="First"), a_project(name="Second")]
    )
    with pytest.raises(ValueError, match="duplicate project ids"):
        load_inventory(path)


def test_distinct_ids_load(tmp_path):
    """The other side of the same guard, so the test above is failing on the
    duplication rather than on the fixture being malformed."""
    path = write_inventory(
        tmp_path, [a_project(id="a"), a_project(id="b", name="Second")]
    )
    assert [p.id for p in load_inventory(path)] == ["a", "b"]


@pytest.mark.parametrize("section", ["amounts", "percents", "years"])
def test_a_whitelist_entry_without_a_why_is_rejected(tmp_path, section):
    """Every whitelist entry is a hole a wrong figure can pass through, so the
    justification is the only thing standing between the file and a number
    nobody can account for. Checked in all three sections because they are
    validated by one loop and a future edit could easily cover only the first.
    """
    path = write_whitelist(tmp_path, {section: [{"value": 7, "kind": "quantity"}]})
    with pytest.raises(ValueError, match="has no 'why'"):
        build_allowed_figures([], whitelist_path=path)


@pytest.mark.parametrize("kind", ["price", "", None])
def test_a_whitelist_entry_with_an_unusable_kind_is_rejected(tmp_path, kind):
    """`kind` decides whether verbalisation may give the figure a
    currency-naming spoken form, and guessing it wrong is the documented way a
    hotline number gets read aloud as a sum of money. A plausible-looking
    invention ("price"), an empty string and an absent key all have to fail:
    the check is `not in`, so anything outside the three kinds is refused
    rather than defaulted.
    """
    entry = {"value": 7, "why": "test fixture"}
    if kind is not None:
        entry["kind"] = kind
    path = write_whitelist(tmp_path, {"amounts": [entry]})
    with pytest.raises(ValueError, match="not one of"):
        build_allowed_figures([], whitelist_path=path)


def test_an_empty_whitelist_loads(tmp_path):
    """`data.get(section) or []` has to survive both a missing section and an
    explicitly null one, which is what a half-finished edit leaves behind."""
    path = write_whitelist(tmp_path, {"amounts": None})
    allowed = build_allowed_figures([], whitelist_path=path)
    assert allowed.amounts == frozenset()


def test_a_project_with_no_optional_fields_serialises_to_its_bare_facts():
    """Every optional field in the prompt line is absent at once.

    Not a contrived shape: a record entered from a launch announcement has a
    name, an area and a unit type and nothing else yet, and the branded-enquiry
    early return does not cover it because this project is `selling`. What the
    line must not do is print an empty price, an open-ended size range or a
    plan with no figures in it, all of which are things a model will read as
    data.
    """
    line = serialise_for_prompt([Project.model_validate(a_project())])
    assert line == "- P (p) | A | selling | units: studio"


def test_a_half_authored_size_range_prints_no_range():
    """`size_sqft_min` without its max. One number is not a range, and
    "420- sqft" is the kind of string a model will happily quote back."""
    project = Project.model_validate(a_project(size_sqft_min=420))
    assert "sqft" not in serialise_for_prompt([project])


def test_a_payment_plan_without_a_price_prints_no_plan():
    """Invariant 2 at the serialisation layer. The percentages are authored and
    the amounts are computed, so a plan with no price to compute from has no
    amounts, and `derive` returns None. The line must then omit the plan
    entirely rather than print the percentages alone, because a bare "20% at
    booking" invites exactly the arithmetic the model may not do.
    """
    project = Project.model_validate(
        a_project(
            payment_plan=[
                {"label": "booking", "pct": 20},
                {"label": "handover", "pct": 80},
            ]
        )
    )
    line = serialise_for_prompt([project])
    assert "plan:" not in line
    assert "20%" not in line
