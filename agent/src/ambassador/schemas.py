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
    # The subset of `amounts` read as a SEQUENCE rather than as a quantity:
    # phone numbers, permit numbers. Also a subset, for the same reason
    # `currency_amounts` is one - the guardrail does not care what a number
    # means, and verbalisation does.
    #
    # It is the mirror image of `currency_amounts`, and the distinction the
    # spoken-form table cannot express on its own. For a square footage the
    # digit fallback is CORRECT and an authored form would be the defect; for
    # an identifier the digit fallback is the defect - 80015 is Binghatti's
    # hotline and TTS reads it as "eighty thousand and fifteen", on the
    # escalation path, which AGENTS.md says gets the same polish as the happy
    # path. So an identifier with no spoken form is a gap the reviewer packet
    # has to enumerate, and a square footage with no spoken form is not.
    #
    # Empty means nothing is enumerated as owed, which is the direction that
    # under-reports rather than the one that walks an author into giving a
    # phone number a currency-naming form.
    identifiers: frozenset[float] = frozenset()


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
    # True only when teardown stranded an unresolved speech handle, so whether
    # the last chunk finished playing is UNKNOWN rather than known-good. It
    # lives on the record and not only on the emitted event because the durable
    # lead keeps it per turn (docs/10-): a stranded final turn has to stay
    # visible as that turn, not just as a call-level flag.
    audit_incomplete: bool = False


# --- Booking (STUB:) ------------------------------------------------------


class BookingRequest(BaseModel):
    slot: str
    language: Language
    shortlist_ids: list[str] = []


# --- Phase 2: leads, scoring and admin decisions --------------------------
#
# docs/02- "Phase 2 lead record" is the mirror of this section. The rule these
# shapes exist to hold is ADR-020's: the model returns text, booleans and turn
# indexes, and never a number. `LeadAnalysisDraft` is therefore what a model may
# produce and `InterestScore` is what only code may produce, and they are two
# types rather than one on purpose - a single model with an optional score is a
# model a prompt can fill in.

ScoreSignal = Literal[
    "budget_stated",
    "project_named",
    "timeline_stated",
    "contact_shared",
    "viewing_or_human_requested",
    "questions_asked",
    "call_length",
]

CallEndReason = Literal[
    "buyer_farewell", "agent_farewell", "duration_cap", "buyer_left", "session_error"
]

AnalysisStatus = Literal["pending", "complete", "failed"]

LeadStatus = Literal["unreviewed", "qualified", "rejected"]

ContactStatus = Literal["not_asked", "captured", "declined", "unconfirmed"]

DecisionReason = Literal[
    "ready",
    "follow_up",
    "not_interested",
    "invalid_contact",
    "outside_scope",
    "duplicate",
    "other",
]

ActorKind = Literal["admin", "user"]


class SignalEvidence(BaseModel):
    """A model's observation, and the turns it is claiming as evidence.

    An observation with no evidence is an assertion. The admin surface shows
    the cited turns as the reason a lead scored what it did, so an observed
    signal that cites nothing would render as a number with an empty
    explanation beside it.
    """

    observed: bool
    turn_indexes: list[int] = []

    @model_validator(mode="after")
    def _observed_needs_evidence(self) -> "SignalEvidence":
        if self.observed and not self.turn_indexes:
            raise ValueError(
                "an observed signal must cite at least one turn in "
                "turn_indexes; an observation with no evidence cannot be "
                "checked against the transcript"
            )
        if not self.observed and self.turn_indexes:
            raise ValueError(
                "turn_indexes must be empty when observed is false, or the "
                "evidence points at a claim nobody made"
            )
        return self


class LeadAnalysisDraft(BaseModel):
    """What the session-analysis model is allowed to return.

    Deliberately carries no score and no points. `ambassador.leads` computes
    those from this plus the facts the model never sees: the contact record,
    the timestamps and the real buyer-turn indexes.
    """

    summary: str
    budget_stated: SignalEvidence
    project_named: SignalEvidence
    project_ids: list[str] = []
    timeline_stated: SignalEvidence
    viewing_or_human_requested: SignalEvidence
    question_turn_indexes: list[int] = []

    @model_validator(mode="after")
    def _project_ids_match_the_signal(self) -> "LeadAnalysisDraft":
        # Both directions are wrong in different ways: ids without the signal
        # is a claim nobody made, the signal without ids is unverifiable.
        if self.project_named.observed and not self.project_ids:
            raise ValueError(
                "project_named is observed but project_ids is empty, so the "
                "claim cannot be resolved against inventory"
            )
        if not self.project_named.observed and self.project_ids:
            raise ValueError(
                "project_ids is set while project_named is not observed; the "
                "ids belong to an observation that was not made"
            )
        return self


class ScoreItem(BaseModel):
    """One line of the explanation. Present even at zero: a signal missing from
    a breakdown reads as "not part of the rubric" rather than "scored
    nothing"."""

    signal: ScoreSignal
    observed: bool
    raw_value: int | bool
    points_awarded: int = Field(ge=0)
    max_points: int = Field(ge=0)
    evidence_turn_indexes: list[int] = []


class InterestScore(BaseModel):
    """Computed only. `score_version` identifies the exact rubric, because
    historic scores are not recomputed when weights change (ADR-020) and a
    total without its version cannot be read afterwards."""

    total: int = Field(ge=0, le=100)
    score_version: str
    breakdown: list[ScoreItem]


class ContactCapture(BaseModel):
    """The one-time contact ask (docs/10-). Values may only come from the reply
    to that ask, which is enforced at capture rather than here."""

    status: ContactStatus
    asked_turn_index: int | None = None
    source_turn_index: int | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    phone_fingerprint: str | None = None
    email_fingerprint: str | None = None
    contact_permission: bool = False
    confirmed: bool = False

    @property
    def has_reachable_value(self) -> bool:
        """A name is not a way to reach anyone. The score and any follow-up
        both need a phone or an email, so the distinction is named once here
        rather than re-derived at each call site."""
        return bool(self.phone or self.email)

    @model_validator(mode="after")
    def _captured_needs_a_value(self) -> "ContactCapture":
        if self.status == "captured" and not self.has_reachable_value:
            raise ValueError(
                "a captured contact needs a phone or an email; a name alone is "
                "not a way to reach anyone"
            )
        if self.status == "declined" and (self.phone or self.email):
            raise ValueError("a declined contact must retain no phone or email value")
        return self


class LeadSnapshot(BaseModel):
    """The durable record of one call, frozen before analysis is attempted.

    Persisted first and idempotently on `session_id`, so a failed analysis
    leaves a lead with `analysis_status=failed` rather than no lead at all
    (ADR-020). `ended_cleanly` and `call_end_reason` keep a truncated call from
    reading like a complete one.
    """

    session_id: str
    started_at: str
    ended_at: str
    call_end_reason: CallEndReason
    ended_cleanly: bool
    language: Language
    requested_language: Language
    uncertified_fallback: bool
    inventory_version: str
    ambassador_name: str = ""
    turns: list[TurnRecord] = []
    brief: "LeadBrief | None" = None
    contact: ContactCapture = ContactCapture(status="not_asked")
    analysis_status: AnalysisStatus = "pending"
    summary: str | None = None
    score: InterestScore | None = None
    status: LeadStatus = "unreviewed"

    @property
    def buyer_turn_indexes(self) -> list[int]:
        """The turns a model may cite as evidence. Derived from the snapshot
        rather than passed in, so evidence is always checked against the record
        that was actually saved."""
        return [turn.turn_index for turn in self.turns]


class AdminDecision(BaseModel):
    """Append-only. The score never writes this; only a person does (ADR-020).

    `expected_lead_revision` is the optimistic check: two admins reading the
    same lead and deciding differently must not silently overwrite each other.
    """

    lead_id: str
    sequence: int = Field(ge=1)
    previous_status: LeadStatus
    new_status: Literal["qualified", "rejected"]
    reason_code: DecisionReason
    note: str | None = None
    actor_kind: ActorKind = "admin"
    actor_id: str | None = None
    created_at: str
    expected_lead_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def _decision_changes_something(self) -> "AdminDecision":
        if self.previous_status == self.new_status:
            raise ValueError(
                f"a decision must change the status; previous and new are both "
                f"{self.new_status!r}"
            )
        return self
