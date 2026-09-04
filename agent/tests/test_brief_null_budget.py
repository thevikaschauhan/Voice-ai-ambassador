"""A buyer who never states a budget must not cost us the whole brief.

god re-ran the production `BriefExtractor._run` against the human's real 08:32Z
transcript (18 messages, rebuilt from the store) on gemini-2.5-flash-lite. The
model did exactly what the prompt asks - "Use null for anything the buyer has
not indicated" - and returned

    "budget": {"amount": null, "currency": null}

`Budget` requires both, so `LeadBrief.model_validate_json` raised on the first
attempt AND on the repair, and the turn fell back. Nine turns out of nine. The
same code with a stated budget passes first attempt on flash-lite and on qwen,
so this is the NO-BUDGET shape, which is most demo calls.

It was invisible in the log because `brief_invalid.error` and `raw` are redacted
- correctly, they quote the offending input back - so ryan's read showed nine
`[redacted]` lines and no way to tell WHICH field was wrong. The locations are
structural, so they can be emitted where the message cannot.

Imports inside each test so RED reads N failed = N cases.
"""

from __future__ import annotations

import json

import pytest

# The exact object the model returned on the human's call.
NULL_BUDGET_BRIEF = {
    "intent": "invest",
    "budget": {"amount": None, "currency": None},
    "unit_preference": "two bedroom",
    "timeline": None,
    "buyer_location": None,
    "golden_visa_interest": None,
    "hesitations": [],
    "shortlist_ids": [],
    "stage": "discovery",
    "language": "en",
}


def test_a_budget_of_nulls_validates_as_no_budget() -> None:
    """The shape from the real call. "The buyer did not say" is information; it
    is not a malformed brief."""
    from ambassador.schemas import LeadBrief

    brief = LeadBrief.model_validate_json(json.dumps(NULL_BUDGET_BRIEF))

    assert brief.budget is None
    assert brief.intent == "invest"
    assert brief.unit_preference == "two bedroom"


async def test_the_call_that_produced_nine_fallbacks_now_produces_a_brief() -> None:
    """The whole defect, through the production path rather than the model.

    One scripted response carrying exactly what flash-lite returned on the
    human's 08:32Z call: first attempt, no repair, a brief with no budget kept
    as the last good one.
    """
    from test_brief import ScriptedTransport, make_extractor

    events: list[tuple] = []
    extractor = make_extractor(
        ScriptedTransport([json.dumps(NULL_BUDGET_BRIEF)]), events
    )

    await extractor.schedule(
        [{"role": "user", "content": "what two bedrooms are there?"}], turn_index=1
    )

    emitted = [name for name, _ in events]
    assert emitted == ["brief"], emitted
    assert extractor.last_good is not None
    assert extractor.last_good.budget is None
    assert extractor.last_good.unit_preference == "two bedroom"


def test_a_budget_of_nulls_beside_a_default_flag_is_also_no_budget() -> None:
    """`confirmed: false` is the model echoing the schema's default, not a
    statement about a budget that does not exist. A rule that only fired for
    the two-key shape would be one model revision from being useless."""
    from ambassador.schemas import LeadBrief

    payload = dict(NULL_BUDGET_BRIEF)
    payload["budget"] = {"amount": None, "currency": None, "confirmed": False}

    assert LeadBrief.model_validate_json(json.dumps(payload)).budget is None


def test_a_half_stated_budget_is_still_rejected() -> None:
    """A GUARD, passing before and after. A currency with no amount is the model
    half-answering, and scoring needs the number: that is a real validation
    failure, and the repair attempt exists for it."""
    from pydantic import ValidationError

    from ambassador.schemas import LeadBrief

    payload = dict(NULL_BUDGET_BRIEF)
    payload["budget"] = {"amount": None, "currency": "AED"}

    with pytest.raises(ValidationError):
        LeadBrief.model_validate_json(json.dumps(payload))


def test_the_tolerance_is_generic_over_optional_sub_objects() -> None:
    """Not `if field == "budget"`.

    An exemption naming one member of a set is a bug waiting on somebody else's
    edit: the next optional sub-object on `LeadBrief` would arrive without it
    and fail exactly the same way, with nothing in a diff to show it.
    """
    from pydantic import BaseModel

    from ambassador.schemas import all_null_optional_objects_to_none

    class Inner(BaseModel):
        required_number: float
        required_text: str
        flagged: bool = False

    class Outer(BaseModel):
        inner: Inner | None = None
        kept: str | None = None

    cleaned = all_null_optional_objects_to_none(
        Outer, {"inner": {"required_number": None, "required_text": None}}
    )

    assert cleaned["inner"] is None
    assert Outer.model_validate(cleaned).inner is None


def test_the_schema_hint_tells_the_model_to_return_null_for_an_unknown_budget() -> None:
    """The validator stops the brief being lost; the hint stops the round trip
    being wasted. The prompt already says "use null for anything the buyer has
    not indicated" and the model applied it to the FIELDS, which is the reading
    the hint invited."""
    from adapter.brief import _schema_hint

    hint = _schema_hint()

    assert '"budget": null' in hint
    assert "budget" in hint


async def test_brief_invalid_names_the_fields_that_failed() -> None:
    """What ryan could not get from nine redacted lines.

    A Pydantic error location is a field path - structural, never buyer text -
    so it can be emitted beside the message that cannot.
    """
    from test_brief import PROJECT_IDS, ScriptedTransport, make_extractor

    payload = dict(NULL_BUDGET_BRIEF)
    payload["budget"] = {"amount": None, "currency": "AED"}  # a real failure
    payload["shortlist_ids"] = [PROJECT_IDS[0]]
    events: list[tuple] = []
    extractor = make_extractor(
        ScriptedTransport([json.dumps(payload), json.dumps(payload)]), events
    )

    await extractor.schedule(
        [{"role": "user", "content": "what is there in dirhams?"}], turn_index=1
    )

    invalid = [fields for name, fields in events if name == "brief_invalid"]
    assert invalid, [name for name, _ in events]
    assert invalid[0]["fields"] == ["budget.amount"]
    assert invalid[0]["error_class"] == "ValidationError"


async def test_brief_fallback_carries_the_locations_of_the_last_failure() -> None:
    """The fallback line is the one an operator reads when the brief is NULL, so
    it is the one that has to say which field lost it."""
    from test_brief import ScriptedTransport, make_extractor

    payload = dict(NULL_BUDGET_BRIEF)
    payload["budget"] = {"amount": None, "currency": "AED"}
    events: list[tuple] = []
    extractor = make_extractor(
        ScriptedTransport([json.dumps(payload), json.dumps(payload)]), events
    )

    await extractor.schedule(
        [{"role": "user", "content": "what is there in dirhams?"}], turn_index=1
    )

    fallback = [fields for name, fields in events if name == "brief_fallback"]
    assert fallback, [name for name, _ in events]
    assert fallback[0]["fields"] == ["budget.amount"]
    assert fallback[0]["error_class"] == "ValidationError"


def test_a_location_that_is_not_an_identifier_is_dropped() -> None:
    """The claim "structural, never buyer text" has to be enforced, not asserted.

    A model can put anything in a key, and an extra-field error's location IS
    that key. Field paths are identifiers and list indexes; anything else is
    someone else's prose and does not go on the stream.
    """
    from pydantic import BaseModel, ConfigDict, ValidationError

    from adapter.brief import error_field_locations

    class Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

        kept: int = 0

    try:
        Strict.model_validate({"my budget is two million": 1, "kept": "no"})
    except ValidationError as exc:
        located = error_field_locations(exc)

    assert located == ["kept"]


def test_the_new_fields_survive_redaction_while_the_message_does_not() -> None:
    """A GUARD, passing before and after: `redact_event` blanks by field name,
    so adding a field to an event is a decision about that field. `fields` and
    `error_class` are enumerable, `error` and `raw` are not."""
    from adapter.events import redact_event

    emitted = redact_event(
        {
            "event": "brief_invalid",
            "turn": 1,
            "attempt": "first",
            "fields": ["budget.amount"],
            "error_class": "ValidationError",
            "error": "1 validation error ... input_value='my budget is two million'",
            "raw": '{"budget": {"amount": null}}',
        }
    )

    assert emitted["fields"] == ["budget.amount"]
    assert emitted["error_class"] == "ValidationError"
    assert "two million" not in json.dumps(emitted)
    assert emitted["raw"] != '{"budget": {"amount": null}}'
