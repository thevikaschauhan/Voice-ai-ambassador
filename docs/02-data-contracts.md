# 02 - Data contracts

The Pydantic models in `agent/src/ambassador/schemas.py` are the executable source of truth for implemented behaviour. This document is the human-readable contract; if the two diverge, fix the divergence in the same change. The Phase 2 contracts below intentionally precede implementation: their first RED card adds the Pydantic shapes before any adapter or route may use them. Do not declare a new type anywhere else without checking here first.

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

## Phase 2 lead record

The durable lead is projected from in-process `TurnRecord`s and the last
accepted `LeadBrief` after brief extraction drains. The redacted event stream
is not an input. Every call persists, including a disconnect or duration cap.

```
LeadRecord
  id                    UUID
  session_id            str              unique idempotency key, never contact data
  created_at/ended_at   datetime         UTC
  call_end_reason       "buyer_farewell" | "agent_farewell" | "duration_cap" | "buyer_left" | "session_error"
  ended_cleanly         bool             false for an unresolved/incomplete final turn
  language              Language
  requested_language    Language
  uncertified_fallback  bool
  inventory_version     str
  brief                 LeadBrief | null encrypted at rest
  contact               ContactCapture
  analysis_status       "pending" | "complete" | "failed"
  summary               str | null        encrypted at rest; model-generated and labelled as such
  score                 InterestScore | null
  status                "unreviewed" | "qualified" | "rejected"
  revision              int               optimistic-concurrency counter
  retention_expires_at  datetime | null    VERIFY: period with Binghatti legal

LeadTurn
  lead_id               UUID
  turn_index            int
  timestamp             datetime
  audit_incomplete      bool
  payload               TurnRecord         authenticated-encryption envelope at rest
  knowledge_use         KnowledgeUseAudit | null
  primary key           (lead_id, turn_index)

EncryptedEnvelope
  algorithm             str               fixed implementation enum, not caller supplied
  key_version           str
  nonce                 bytes
  ciphertext            bytes
```

`LeadTurn.payload`, `brief`, `summary`, contact values and admin notes bind the
lead id plus field path as authenticated associated data. The API returns their
ordinary domain shape only after authentication; the persistence envelope never
leaks into the web contract.

### Contact capture

```
ContactCapture
  status                "not_asked" | "captured" | "declined" | "unconfirmed"
  asked_turn_index      int | null
  source_turn_index     int | null
  name                  str | null          encrypted at rest
  phone                 str | null          canonical, encrypted at rest
  email                 str | null          canonical, encrypted at rest
  phone_fingerprint     str | null          keyed HMAC-SHA-256, indexed for equality only
  email_fingerprint     str | null          keyed HMAC-SHA-256, indexed for equality only
  contact_permission    bool
  confirmed             bool
```

Only the reply to the one contact request can populate these fields. Phone and
email must match a literal span in that reply before normalization. At least
one of phone/email is required when `status=captured`; both values are null for
`declined`; `contact_permission` must be true before a clear contact value is
retained. A phone echo is rendered as a digit sequence from reviewer-authored
forms, not generated by the model.

### Summary signals and deterministic scoring

The session analysis model returns `LeadAnalysisDraft`, never
`InterestScore`. Every evidence index must resolve to an existing buyer turn.
The scorer counts, buckets and adds in pure code using the versioned rubric in
`data/interest-score.yaml`.

```
LeadAnalysisDraft
  summary                         str
  budget_stated                   SignalEvidence
  project_named                   SignalEvidence
  project_ids                     [str]       every id resolves to inventory
  timeline_stated                 SignalEvidence
  viewing_or_human_requested      SignalEvidence
  question_turn_indexes           [int]       unique buyer turns; code counts them

SignalEvidence
  observed              bool
  turn_indexes          [int]

InterestScore
  total                 int              0..100, computed only
  score_version         str
  breakdown             [ScoreItem]

ScoreItem
  signal                "budget_stated" | "project_named" | "timeline_stated" |
                        "contact_shared" | "viewing_or_human_requested" |
                        "questions_asked" | "call_length"
  observed              bool
  raw_value             int | bool
  points_awarded        int              computed only
  max_points            int
  evidence_turn_indexes [int]
```

`project_ids` is empty exactly when `project_named.observed=false`; every id
must resolve through the inventory loader and the cited buyer turns must contain
the corresponding project mention after the existing deterministic name
normalisation. The initial maximum points are 15, 15, 10, 20, 25, 10 and 5 in
the signal order above. Questions earn 5 points per distinct validated turn,
capped at 2;
call length earns one point per complete 60 seconds, capped at 5. The rubric
loader requires all seven signals exactly once and a maximum of 100. A new
weight set creates a new `score_version`; historic scores are not recomputed.

### Admin decision

```
AdminDecision
  id                    UUID
  lead_id               UUID
  sequence              int
  previous_status       "unreviewed" | "qualified" | "rejected"
  new_status            "qualified" | "rejected"
  reason_code           "ready" | "follow_up" | "not_interested" |
                        "invalid_contact" | "outside_scope" | "duplicate" | "other"
  note                  str | null       encrypted at rest
  actor_kind            "admin" | "user"
  actor_id              UUID | null      null for the shared-code POC
  created_at            datetime
  expected_lead_revision int             rejected on a concurrent change
```

Rows are append-only. `LeadRecord.status` is the latest committed decision;
changing it and appending the decision happen in one transaction. The score is
never an admin decision and no threshold changes status automatically.

## Phase 2 knowledge contracts

```
KnowledgeDocument
  id                    UUID
  revision              int
  title                 str
  source_type           "pdf" | "docx" | "txt" | "paste"
  original_filename     str | null
  mime_type             str
  source_bytes           int
  source_sha256         str
  status                "parsing" | "draft" | "published" | "failed" | "archived"
  parse_error_code      "unsupported_type" | "invalid_encoding" |
                        "limit_exceeded" | "no_extractable_text" | "malformed" | null
  extracted_text        str              admin data; never an emitted event field
  created_at/updated_at datetime
  published_at          datetime | null
  tenant_id             UUID | null       null in the single-tenant POC
  primary key           (id, revision)

KnowledgeChunk
  id                    UUID
  document_id           UUID
  document_revision     int
  ordinal               int
  heading               str | null
  body                  str
  retrieval_scope       "admin_only" | "general_knowledge" | "inventory_governed"
  scope_review_id       UUID | null       null means closed `admin_only`
  prompt_body           str | null       null unless reviewed general knowledge;
                                            unapproved occurrences replaced
  page_start/page_end   int | null
  content_sha256        str
  search_vector         Postgres tsvector using the "simple" configuration

KnowledgeFigure
  id                    UUID
  document_id           UUID
  document_revision     int
  chunk_id              UUID
  value                 decimal
  kind                  "amount" | "percent" | "year" | "count"
  currency              str | null
  unit                  str | null
  surface               str
  source_sentence       str
  page                  int | null
  active_approval_id    UUID | null

KnowledgeFigureReview
  id                    UUID
  figure_id             UUID
  action                "approved" | "revoked"
  actor_kind            "admin" | "user"
  actor_id              UUID | null
  created_at            datetime

KnowledgeChunkReview
  id                    UUID
  chunk_id              UUID
  action                "general_knowledge" | "inventory_governed" | "admin_only"
  actor_kind            "admin" | "user"
  actor_id              UUID | null
  created_at            datetime
```

Parsing creates figures but never approves them. `active_approval_id` is a
projection of the append-only review history. A chunk defaults to `admin_only`.
Project names, locations, prices, sizes, handover, payment structures and
amenities are `inventory_governed`: they can be inspected in admin, but their
document prose never becomes `prompt_body`; the canonical value continues to
come from `data/inventory.json`. Publishing controls retrieval of reviewed
`general_knowledge` chunks; the current `scope_review_id` is a projection of an
append-only review history. Figure approval controls only whether an occurrence
remains in their `prompt_body` and can join a turn's allowed set, and cannot
override chunk scope. Archiving a document or revoking a figure affects new
turns without erasing the revision cited by historic turns.

```
RetrievedKnowledge
  turn_index            int
  query_fingerprint     str              keyed digest; never the buyer query
  chunks                [RetrievedChunk] maximum 4
  approved_figures      [KnowledgeFigure]
  withheld_figure_match bool
  elapsed_ms            int

RetrievedChunk
  chunk_id              UUID
  document_id           UUID
  document_revision     int
  rank                  float
  prompt_body           str

KnowledgeUseAudit
  turn_index            int
  query_fingerprint     str              keyed digest; never the buyer query
  chunk_refs            [(chunk_id, document_id, document_revision)]
  figure_review_ids     [UUID]
  withheld_figure_match bool
  elapsed_ms            int
```

The prompt builder accepts `RetrievedKnowledge`, appends one delimited
reference-data message to a copy of the chat context, and extends a copy of the
base `AllowedFigures` only with approved occurrences in eligible
`general_knowledge` chunks. The query and excerpts never enter the emitted
event stream. `KnowledgeUseAudit` freezes only the ids, immutable revisions and
figure-review ids used by a turn, so later revocation cannot rewrite what the
agent had been allowed to see.

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

### Phase 2 emitted events

These events extend the existing catalog. Every one is admitted to
`CLEAR_EVENTS` with a reason or is redacted before emission. Fields not listed
below are forbidden on the clear stream.

| Event | Clear fields | Explicitly absent |
|---|---|---|
| `lead_snapshot_persisted` | opaque lead id, turn count, `ended_cleanly`, call-end enum | transcript, brief, contact, summary, score |
| `lead_persist_failed` | stage enum, error-code enum, attempt count | exception/detail, session text, contact |
| `lead_analysis` | opaque lead id, outcome enum, attempt count, latency, score-version id | summary, evidence text, score |
| `contact_policy` | turn index, action enum, asked count, confirmed/declined booleans | name, phone, email, echo text |
| `knowledge_document_ingested` | opaque document id, revision, source-type enum, status enum, byte/chunk/figure counts | title, filename, extracted text, parse exception |
| `knowledge_chunk_reviewed` | opaque document/chunk/review ids, scope enum, revision | heading, body, prompt body, reviewer note |
| `knowledge_figure_reviewed` | opaque document/figure/review ids, action enum, revision | value, surface, source sentence, page |
| `knowledge_retrieval` | turn index, outcome enum, chunk/approved/withheld counts, elapsed milliseconds | query, titles, filenames, excerpts, figure values |
| `admin_decision_recorded` | opaque lead/decision ids, previous/new status, actor kind, revision | reason note, contact, summary, transcript |

The admin API persists an audit copy of these classified records. It does not
make the full-fidelity bridge durable. New event discovery tests continue to
fail when an emitted name is absent from both the redacted map and
`CLEAR_EVENTS`.

## Booking (STUB:)

```
BookingRequest   {slot: str, language: Language, shortlist_ids: [str]}
```

Spoken read-back confirmation only. No calendar API in the POC; the interface exists so the integration point is visible.
