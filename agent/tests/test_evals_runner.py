"""What the buyer HEARS, turn by turn, when the harness drives the pipeline.

Every assertion here is on spoken text or on an action a human would notice -
never on the objects the runner returned. The repo learned that distinction the
hard way (AGENTS.md, 2026-08-31): eight defects shipped green under a suite that
asserted on decision objects while the speech was wrong.
"""

from __future__ import annotations

import pytest

from adapter.fallbacks import load_fallback_copy
from evals.backends import ModelReply, ModelRequest
from evals.cases import EvalCase
from evals.runner import Harness, run_case

COPY = load_fallback_copy()


class ScriptedBackend:
    """Replies in order, so a test can hand the pipeline exactly the model
    output it wants without authoring a case file."""

    name = "scripted"

    def __init__(self, *replies: ModelReply) -> None:
        self._replies = list(replies)
        self.requests: list[ModelRequest] = []

    def reply(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the pipeline asked for more replies than scripted")
        return self._replies.pop(0)


def case(buyer: str | list[str], *, language="en", assertions=None) -> EvalCase:
    turns = [buyer] if isinstance(buyer, str) else buyer
    return EvalCase.model_validate(
        {
            "id": "t",
            "category": "grounding_happy_path",
            "language": language,
            "turns": [{"buyer": b} for b in turns],
            "assertions": assertions or [{"kind": "must_not_escalate"}],
        }
    )


@pytest.fixture(scope="module")
def harness() -> Harness:
    return Harness.load()


def spoken(observed) -> str:
    return observed.spoken_text


# --- the guardrail, from the buyer's side ---------------------------------


def test_an_inventory_figure_is_spoken_in_its_spoken_form(harness):
    backend = ScriptedBackend(
        ModelReply("Binghatti Skyrise starts from AED 985,000.")
    )
    observed = run_case(case("What does Skyrise cost?"), harness, backend)
    assert spoken(observed) == (
        "Binghatti Skyrise starts from nine hundred and eighty-five thousand dirhams."
    )
    assert not observed.blocked


def test_a_fabricated_figure_never_reaches_the_buyer(harness):
    backend = ScriptedBackend(
        ModelReply("Binghatti Skyrise starts from AED 750,000."),
        ModelReply("I am not able to confirm that figure."),
    )
    observed = run_case(case("Is it 750k?"), harness, backend)
    assert "750" not in spoken(observed)
    assert spoken(observed) == "I am not able to confirm that figure."
    assert observed.regenerated


def test_the_regeneration_names_the_violation_to_the_model(harness):
    backend = ScriptedBackend(
        ModelReply("Binghatti Skyrise starts from AED 750,000."),
        ModelReply("I cannot confirm that."),
    )
    run_case(case("Is it 750k?"), harness, backend)
    retry = backend.requests[-1]
    assert retry.regeneration_detail is not None
    assert "750,000" in retry.regeneration_detail
    assert "INVENTORY" in retry.regeneration_detail


def test_a_second_bad_reply_hands_the_buyer_the_composed_fallback(harness):
    """The regeneration is spent, so the composed copy IS the reply - and it is
    the copy in data/fallbacks.yaml, not a string this harness invented."""
    backend = ScriptedBackend(
        ModelReply("It starts from AED 750,000."),
        ModelReply("The price is AED 750,000."),
    )
    observed = run_case(case("Is it 750k?"), harness, backend)
    assert spoken(observed) == COPY.fallback["en"]
    assert [s.origin for s in observed.heard] == ["fallback"]


def test_audio_already_played_gets_a_bridge_and_spends_no_retry(harness):
    """A blind mid-turn retry would repeat or contradict what the buyer just
    heard, so the second sentence is replaced rather than regenerated."""
    backend = ScriptedBackend(
        ModelReply(
            "Binghatti Skyrise is in Business Bay. It starts from AED 750,000."
        )
    )
    observed = run_case(case("Where is Skyrise?"), harness, backend)
    assert spoken(observed) == (
        f"Binghatti Skyrise is in Business Bay. {COPY.bridge['en']}"
    )
    assert not observed.regenerated
    assert [s.origin for s in observed.heard] == ["model", "bridge"]


def test_a_blocked_reply_never_enters_the_next_turn_context(harness):
    """The model's next turn is grounded in what the buyer HEARD. Feeding it the
    blocked sentence would ground the conversation in speech nobody said."""
    backend = ScriptedBackend(
        ModelReply("It starts from AED 750,000."),
        ModelReply("The price is AED 750,000."),
        ModelReply("Binghatti Circle starts from AED 650,000."),
    )
    observed = run_case(
        case(["Is it 750k?", "What about Circle?"]), harness, backend
    )
    history = dict.fromkeys(
        content for role, content in backend.requests[-1].messages if role == "assistant"
    )
    assert "750,000" not in " ".join(history)
    assert COPY.fallback["en"] in " ".join(history)
    assert "six hundred and fifty thousand dirhams" in spoken(observed)


def test_prohibited_language_is_blocked_even_with_no_figure_in_it(harness):
    backend = ScriptedBackend(
        ModelReply("This is essentially risk-free."),
        ModelReply("No investment is without risk."),
    )
    observed = run_case(case("Is it safe?"), harness, backend)
    assert "risk-free" not in spoken(observed)
    assert observed.blocked[0].validator == "prohibited_language"


def test_the_regenerated_reply_s_tool_call_counts_as_an_escalation(harness):
    """The regeneration is a fresh generation and its tool calls reach the
    framework exactly as the first attempt's would. Crediting only the first
    would score a model that refused correctly the moment it was told why as a
    model that never escalated."""
    backend = ScriptedBackend(
        ModelReply("Bugatti Residences starts from AED 19,000,000."),
        ModelReply(
            "I am not able to quote that.", tools=("escalate_to_human",)
        ),
    )
    observed = run_case(case("What is Bugatti?"), harness, backend)
    assert observed.escalated
    assert "regenerated" in observed.escalation_reasons[0]


# --- the deterministic budget confirmation (ADR-011) ----------------------


def test_the_policy_speaks_instead_of_the_model_on_a_first_budget_mention(harness):
    backend = ScriptedBackend()  # any model call would raise
    observed = run_case(case("My budget is about 2 crore."), harness, backend)
    assert spoken(observed) == "2 crore - is that in dirhams or in rupees?"
    assert observed.confirmed
    assert not backend.requests, "the model was called on a confirmation turn"


def test_the_confirmation_echoes_the_buyer_verbatim_and_is_not_verbalised(harness):
    """Verbalising the echo would assert a currency on the exact turn whose
    purpose is to ask which one the buyer meant."""
    observed = run_case(
        case("My budget is 985,000 dirhams."), harness, ScriptedBackend()
    )
    assert spoken(observed) == "985,000 - have I got that right?"
    assert "dirhams" not in spoken(observed).replace("985,000", "")


def test_three_unanswered_confirmations_hand_the_buyer_to_a_human(harness):
    observed = run_case(
        case(
            [
                "My budget is about 2 crore.",
                "Sorry, could you repeat that?",
                "I did not catch you.",
                "Say that again please.",
            ]
        ),
        harness,
        ScriptedBackend(),
    )
    assert observed.escalated
    assert spoken(observed).endswith(
        "Let me put you through to one of our ambassadors who can go through "
        "the numbers with you properly."
    )


def test_a_rupee_budget_is_handed_over_rather_than_converted(harness):
    """data/currencies.yaml ships no confirmed rate, so a conversion would be a
    specific, checkable, wrong number said with confidence."""
    observed = run_case(
        case(["My budget is about 2 crore.", "In rupees."]), harness, ScriptedBackend()
    )
    assert observed.escalated
    assert "880" not in spoken(observed)
    assert "convert" in spoken(observed)


def test_a_broken_confirmation_template_fails_closed_to_a_human(harness):
    """The failure direction PR #20's review fixed: any error on the
    confirmation path hands over. Falling through to the model would let it
    answer on an unconfirmed budget, which is the twenty-times error."""
    broken = Harness(
        **{
            **{f.name: getattr(harness, f.name) for f in harness.__dataclass_fields__.values()},
            "confirmations": harness.confirmations.__class__(
                by_language={
                    "en": {
                        **harness.confirmations.by_language["en"],
                        "ask_currency": "{ammount} - dirhams or rupees?",
                    },
                    "ar": harness.confirmations.by_language["ar"],
                    "hi": harness.confirmations.by_language["hi"],
                }
            ),
        }
    )
    observed = run_case(
        case("My budget is about 2 crore."), broken, ScriptedBackend()
    )
    assert observed.escalated
    assert spoken(observed) == broken.confirmations.line("en", "give_up")
    assert "{" not in spoken(observed)


def test_the_policy_is_off_where_no_confirmation_copy_exists(harness):
    """Speaking English into a Hindi call would be worse than not asking, so the
    model keeps the turn and the guardrail is what catches the conversion."""
    backend = ScriptedBackend(
        ModelReply("दो करोड़ रुपये लगभग AED 880,000 होते हैं।"),
        ModelReply("दो करोड़ रुपये लगभग AED 880,000 के बराबर हैं।"),
    )
    observed = run_case(
        case("मेरा बजट दो करोड़ है।", language="hi"), harness, backend
    )
    assert not observed.confirmed
    assert "880" not in spoken(observed)
    assert spoken(observed) == COPY.fallback["hi"]


# --- the finding this harness surfaced ------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING: the composed fallback says 'let me put you through to one of "
        "our ambassadors' and nobody is put through. adapter/agent.py wires the "
        "fallback sink to tracker.record_fallback, which appends a chunk and "
        "emits an event; it never calls _route_to_human. data/fallbacks.yaml "
        "describes that copy as 'the line that hands the buyer to a human', and "
        "_route_to_human's own docstring names this exact anti-pattern. Not "
        "fixed here: this harness does not change product behaviour to make its "
        "rows green. Flip this to a plain assertion when the wiring lands."
    ),
)
def test_the_fallback_hands_the_buyer_to_a_human(harness):
    backend = ScriptedBackend(
        ModelReply("Binghatti Sapphire Bay starts from AED 1,450,000."),
        ModelReply("The price at Binghatti Sapphire Bay is AED 1,450,000."),
    )
    observed = run_case(case("What is Sapphire Bay?"), harness, backend)
    assert "put you through" in spoken(observed)
    assert observed.escalated


def test_a_year_followed_by_a_comma_is_blocked(harness):
    """FINDING, pinned as current behaviour so the fix is visible when it lands.

    `figures.py`'s number pattern captures a trailing comma into the surface
    ("2026,"), and `_classify` refuses to call a comma-bearing surface a year.
    So an ALLOWED handover year is reclassified as an unallowed AMOUNT and a
    correct, grounded sentence is blocked - the buyer hears the fallback instead
    of the handover date. Amounts and counts are unaffected, because the comma is
    stripped before their value is read; only the year branch checks the surface.
    """
    backend = ScriptedBackend(
        ModelReply("Handover is Q4 2026, and the plan runs to then."),
        ModelReply("Handover is Q4 2026, as listed."),
    )
    observed = run_case(case("When is handover?"), harness, backend)
    assert spoken(observed) == COPY.fallback["en"]
    assert observed.blocked[0].figures[0].surface == "2026,"
    assert observed.blocked[0].figures[0].kind == "amount"

    # The same sentence with a full stop passes, which is what makes this a
    # punctuation defect rather than a missing whitelist entry.
    ok = run_case(
        case("When is handover?"),
        harness,
        ScriptedBackend(ModelReply("Handover is Q4 2026.")),
    )
    assert spoken(ok) == "Handover is the fourth quarter of 2026."


# --- the one piece of logic shared with the streaming path ----------------


def test_the_harness_and_the_adapter_split_sentences_identically():
    """The boundary regex lives in `ambassador.sentences` and both callers import
    it. A second copy would drift, and the drift has a known shape: the Arabic
    `؟` and the Devanagari `।` are the easy omissions, and the miss would show up
    as an eval passing in English while the live call mis-splits in Arabic."""
    pytest.importorskip("livekit.agents", reason="voice dependency group not installed")
    from adapter.interception import split_sentences as adapter_split
    from ambassador.sentences import split_sentences as core_split

    assert adapter_split is core_split
    for text in (
        "One. Two? Three!",
        "ما هو السعر؟ وما هو الموعد؟",
        "कीमत क्या है। हैंडओवर कब है।",
        "AED 1.5 million and Q4 2026 stay whole",
    ):
        assert core_split(text) == adapter_split(text)
