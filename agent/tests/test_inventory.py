import pytest
from pydantic import ValidationError

from ambassador.inventory import derive, serialise_for_prompt
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
