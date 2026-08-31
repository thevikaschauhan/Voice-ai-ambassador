"""Source of truth for every type in the system.

docs/02-data-contracts.md is the human-readable mirror of this file; if the two
diverge, fix the divergence in the same change.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Language = Literal["en", "ar", "hi"]

ProjectStatus = Literal["selling", "branded_enquiry", "sold_out"]

FigureKind = Literal["amount", "percent", "year", "count"]

ValidatorName = Literal["numeric_claims", "prohibited_language"]


# --- Inventory -----------------------------------------------------------


class Milestone(BaseModel):
    label: str
    pct: float = Field(gt=0, le=100)


class Handover(BaseModel):
    quarter: int = Field(ge=1, le=4)
    year: int = Field(ge=2020, le=2099)


class Project(BaseModel):
    id: str
    name: str
    area: str
    status: ProjectStatus
    price_from_aed: int | None = None
    unit_types: list[str]
    size_sqft_min: int | None = None
    size_sqft_max: int | None = None
    handover: Handover | None = None
    payment_plan: list[Milestone] | None = None
    amenities: list[str] = []
    source_ref: str
    last_verified: str | None = None

    @model_validator(mode="after")
    def _invariants(self) -> "Project":
        if self.status == "branded_enquiry" and self.price_from_aed is not None:
            raise ValueError(
                f"{self.id}: branded_enquiry projects must not carry a price "
                "(invariant: branded pricing is never quoted)"
            )
        if self.payment_plan is not None:
            total = sum(m.pct for m in self.payment_plan)
            if abs(total - 100.0) > 0.01:
                raise ValueError(
                    f"{self.id}: payment plan percentages sum to {total}, not 100"
                )
        return self


class DerivedFigures(BaseModel):
    """Computed at load time by inventory.derive(); never hand-authored."""

    milestone_amounts_aed: list[int]


@dataclass(frozen=True)
class AllowedFigures:
    """The complete set of figures the system is permitted to speak."""

    amounts: frozenset[float]
    percents: frozenset[float]
    years: frozenset[int]
    # The subset of `amounts` that is MONEY, and the set a PRICE is checked
    # against. Two consumers need it. Verbalisation, because only a currency
    # amount may take a spoken form that names a currency and swallows an
    # adjacent "AED": `amounts` also holds square footages and the hotline
    # number, and a currency-naming form on one of those makes the buyer hear
    # "four hundred and twenty dirhams square feet". And the numeric guardrail,
    # because a figure with a currency token beside it is claiming a price -
    # checking those against the untyped `amounts` let "It starts at AED 380"
    # validate against a square footage and "It starts at AED 80015" against
    # the hotline number.
    #
    # It defaults to empty, and empty means every currency-adjacent figure is
    # blocked. That is the safe direction on purpose: an under-populated set
    # blocks sentences, it never speaks an unverified figure.
    currency_amounts: frozenset[float] = frozenset()


# --- Guardrail results ----------------------------------------------------


class ExtractedFigure(BaseModel):
    surface: str
    value: float
    kind: FigureKind


class GuardrailViolation(BaseModel):
    validator: ValidatorName
    detail: str
    figures: list[ExtractedFigure] = []


@dataclass(frozen=True)
class ValidatedSentence:
    """Produced only by guardrails.pipeline.run_guardrails(). Do not construct
    elsewhere; doing so bypasses validation and is a defect."""

    text: str
    language: Language


@dataclass(frozen=True)
class SpeakableText:
    """Produced only by verbalise(). The only type a TTS adapter may accept."""

    text: str
    language: Language


# --- Lead brief -----------------------------------------------------------


class Budget(BaseModel):
    amount: float
    currency: str
    confirmed: bool = False


Stage = Literal[
    "opening", "discovery", "recommendation", "objections", "booking", "escalated"
]


class LeadBrief(BaseModel):
    intent: Literal["invest", "live", "unknown"] = "unknown"
    budget: Budget | None = None
    unit_preference: str | None = None
    timeline: str | None = None
    buyer_location: str | None = None
    golden_visa_interest: bool | None = None
    hesitations: list[str] = []
    shortlist_ids: list[str] = []
    stage: Stage = "opening"
    language: Language


# --- Events and audit -----------------------------------------------------


class SpokenChunk(BaseModel):
    text: str
    completed: bool  # False when barge-in cut playback of this chunk


class Timings(BaseModel):
    endpoint: float | None = None
    stt: float | None = None
    llm_first_sentence: float | None = None
    guardrail: float | None = None
    tts_first_audio: float | None = None
    total: float | None = None


class TurnRecord(BaseModel):
    session_id: str
    turn_index: int
    timestamp: str
    buyer_utterance: str
    generated_sentences: list[str]
    spoken_chunks: list[SpokenChunk]
    guardrail_decisions: list[GuardrailViolation]
    actions: list[str] = []
    timings_ms: Timings = Timings()
    inventory_version: str
    model: str
    prompt_mode: Literal["ambassador", "naive"]
    guardrail_mode: Literal["enforce", "warn"]


# --- Booking (STUB:) ------------------------------------------------------


class BookingRequest(BaseModel):
    slot: str
    language: Language
    shortlist_ids: list[str] = []
