# 02 - Data contracts

The Pydantic models in `agent/src/ambassador/schemas.py` are the executable source of truth. This document is the human-readable contract; if the two diverge, fix the divergence in the same change. Do not declare a new type anywhere else without checking here first.

## Inventory

`data/inventory.json` - the system of record for the POC. Hand-authored placeholders until Binghatti supplies a price sheet (assumption A3); every figure is implicitly `VERIFY:`.

```
Project
  id                str          slug, e.g. "binghatti-skyrise"
  name              str          display and spoken name
  area              str          e.g. "Business Bay"
  status            "selling" | "branded_enquiry" | "sold_out"
  price_from_aed    int | null   null iff status == "branded_enquiry" (never quote branded prices)
  unit_types        [str]        e.g. ["studio", "1br", "2br"]
  size_sqft_min/max int | null
  handover          {quarter: 1-4, year: int} | null
  payment_plan      [Milestone] | null
  amenities         [str]
  source_ref        str          where the placeholder figures came from
  last_verified     str | null   ISO date; null means unverified placeholder

Milestone
  label             str          e.g. "booking", "during construction", "handover"
  pct               float        of price_from_aed; plan percentages must sum to 100
```

### Computed at load time, never authored

`inventory.py` derives, for every selling project with a price and plan:

```
DerivedFigures
  milestone_amounts_aed   [int]   round(price_from_aed * pct / 100) per milestone
```

Derived figures are injected into the serialised prompt block and into the allowed figure set. Hand-authoring a derived number in the JSON is a defect: computed figures cannot be mistyped, authored ones can.

### Allowed figure set

Built by `inventory.py` from the whole inventory plus `data/whitelist.yaml`:

```
AllowedFigures
  amounts           set[float]   prices, sizes, milestone amounts, whitelist amounts
  percents          set[float]   payment plan percentages, whitelist percents
  years             set[int]     handover years, whitelist years
  currency_amounts  set[float]   the subset of `amounts` that is MONEY
  identifiers       set[float]   the subset of `amounts` read as a SEQUENCE
```

`amounts` is what the guardrail checks: to it a number is a number, and that is
correct. The two subsets exist because verbalisation is not indifferent, and
they are the two ends of one question - what the digit fallback does to this
figure. A square footage is in neither: digits read it correctly, and a
currency-naming form on it would make a buyer hear "four hundred and twenty
dirhams square feet". A price is a `currency_amount`: its spoken form names the
currency and swallows an adjacent written "AED". The hotline is an
`identifier`: digits read it as "eighty thousand and fifteen", so it is owed a
spoken form and the reviewer packet asks for one, separately and with different
instructions from the money.

`data/whitelist.yaml` entries each carry a `why` and a `kind`
(`currency | quantity | identifier`); the kind is what populates those two
subsets. Keep the `why` short; every entry is a hole a wrong number could pass
through.

## Guardrail results

```
GuardrailViolation
  validator   "numeric_claims" | "prohibited_language"
  detail      str                     human-readable, for the violation log and demo UI
  figures     [ExtractedFigure]       numeric_claims only

ExtractedFigure
  surface     str      as it appeared, e.g. "2.4 crore", "٩٧٥٬٠٠٠"
  value       float    canonical, e.g. 24000000.0
  kind        "amount" | "percent" | "year" | "count"

ValidatedSentence      produced only by run_guardrails
  text        str
  language    "en" | "ar" | "hi"

SpeakableText          produced only by verbalise; the only type TTS accepts
  text        str
  language    Language
```

## Lead brief

Extracted asynchronously per turn by the small model; Pydantic-validated; shown on the ambassador view. Contact details are not captured by voice in the POC (STT of phone numbers is error-prone and the web form already has them).

```
LeadBrief
  intent            "invest" | "live" | "unknown"
  budget            {amount: float, currency: str, confirmed: bool} | null
  unit_preference   str | null
  timeline          str | null
  buyer_location    str | null
  golden_visa_interest  bool | null
  hesitations       [str]
  shortlist_ids     [str]      every id MUST resolve to a real inventory record (validator 3)
  stage             "opening" | "discovery" | "recommendation" | "objections" | "booking" | "escalated"
  language          Language
```

Budget currency is stored as stated by the buyer. Any conversion to AED is deterministic code with a pinned rate marked `VERIFY:`, never the model, and the conversion is spoken back for confirmation before it drives a recommendation.

## Events and audit

Every turn emits one `TurnRecord`. Full fidelity (utterance text, agent sentences, brief PII) is retained in the POC's in-memory records only; the emitted JSON event stream carries enumerated/numeric telemetry with all free-text fields redacted (validator 4 in `docs/03-`). `PHASE-2:` hashing before durable storage. Event types on the emitted stream include: `user_turn`, `guardrail`, `bridge`, `fallback`, `regeneration`, `tool_call`, `escalation`, `booking_offered`, `brief`, `brief_invalid`, `brief_stale_dropped`, `llm_request`, `llm_usage`, `llm_failure`, `event_log_backpressure`, `turn_complete` - the latency meter and any consumer must tolerate new types.

On the voice path the model starts work BEFORE the final transcript exists: LiveKit's `preemptive_generation` is on by default, so `llm_node` runs on a partial and the final transcript is adopted onto that same turn (`turn_complete.preemptive: true`). One buyer turn is still exactly one `TurnRecord` - opening a second one there split the LLM and guardrail work away from the endpointing and audio marks, which the first live audio run measured. `buyer_utterance` is the final text; the timings start from when the model began, which is earlier than the final transcript and is the honest answer to "how long did the buyer wait".

Turns seal when their speech handle resolves, not at the agent's "listening" transition (the framework pauses and goes to "listening" before an interruption is confirmed). `turn_complete` carries `audit_incomplete: bool` - true only when teardown stranded an unresolved handle; consumers should flag or exclude those rows.

```
TurnRecord
  session_id, turn_index, timestamp
  buyer_utterance      str          final STT text
  generated_sentences  [str]        what the model produced
  spoken_chunks        [SpokenChunk] what was actually synthesised and played
  guardrail_decisions  [GuardrailViolation]
  actions              [str]        tool calls fired
  timings_ms           {endpoint, stt, llm_first_sentence, guardrail, tts_first_audio, total}
  inventory_version    str
  model, prompt_mode, guardrail_mode

SpokenChunk
  text        str
  completed   bool     false if barge-in cut playback of this chunk
```

The audit claim is chunk-granular: a barge-in mid-chunk marks that chunk `completed: false`. We do not claim word-level truncation fidelity in the POC (`docs/04-`).

## Booking (STUB:)

```
BookingRequest   {slot: str, language: Language, shortlist_ids: [str]}
```

Spoken read-back confirmation only. No calendar API in the POC; the interface exists so the integration point is visible.
