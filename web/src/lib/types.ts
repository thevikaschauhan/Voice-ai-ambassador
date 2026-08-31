/**
 * TypeScript mirror of the agent's own types. Nothing here is invented.
 *
 *   agent/src/ambassador/schemas.py   the models
 *   agent/src/adapter/events.py       the event names and their fields
 *   docs/02-data-contracts.md         the human-readable mirror of both
 *
 * If this file and `schemas.py` ever disagree, `schemas.py` wins and the
 * divergence is fixed in the same change.
 *
 * FIDELITY. These types describe the IN-PROCESS records, which is what the
 * demo surface consumes (issue #9: "data comes from the in-process records,
 * not the emitted event stream"). The stdout/file stream carries the same
 * event names with every free-text field replaced by `REDACTED` - see
 * `_REDACTED_FIELDS` in events.py. A consumer of that stream would type the
 * free-text fields as `string | typeof REDACTED`; this surface never reads it.
 */

// --- enums, straight off schemas.py --------------------------------------

export type Language = 'en' | 'ar' | 'hi'
export type ProjectStatus = 'selling' | 'branded_enquiry' | 'sold_out'
export type FigureKind = 'amount' | 'percent' | 'year' | 'count'
export type ValidatorName = 'numeric_claims' | 'prohibited_language'
export type PromptMode = 'ambassador' | 'naive'
export type GuardrailMode = 'enforce' | 'warn'

export type Stage =
  | 'opening'
  | 'discovery'
  | 'recommendation'
  | 'objections'
  | 'booking'
  | 'escalated'

export const STAGES: readonly Stage[] = [
  'opening',
  'discovery',
  'recommendation',
  'objections',
  'booking',
  'escalated',
]

/** events.py: the sentinel every redacted field is replaced by on the stream. */
export const REDACTED = '[redacted]'

// --- inventory ------------------------------------------------------------

export interface Milestone {
  label: string
  pct: number
}

export interface Handover {
  quarter: number
  year: number
}

export interface Project {
  id: string
  name: string
  area: string
  status: ProjectStatus
  price_from_aed: number | null
  unit_types: string[]
  size_sqft_min: number | null
  size_sqft_max: number | null
  handover: Handover | null
  payment_plan: Milestone[] | null
  amenities: string[]
  source_ref: string
  last_verified: string | null
}

// --- guardrail results ----------------------------------------------------

export interface ExtractedFigure {
  surface: string
  value: number
  kind: FigureKind
}

export interface GuardrailViolation {
  validator: ValidatorName
  detail: string
  figures: ExtractedFigure[]
}

// --- lead brief -----------------------------------------------------------

export interface Budget {
  amount: number
  currency: string
  confirmed: boolean
}

export interface LeadBrief {
  intent: 'invest' | 'live' | 'unknown'
  budget: Budget | null
  unit_preference: string | null
  timeline: string | null
  buyer_location: string | null
  golden_visa_interest: boolean | null
  hesitations: string[]
  shortlist_ids: string[]
  stage: Stage
  language: Language
}

// --- audit ---------------------------------------------------------------

export interface SpokenChunk {
  text: string
  /** false when barge-in cut playback of this chunk (docs/04-). */
  completed: boolean
}

/**
 * Milliseconds. `null` means NOT OBSERVED, and the meter must render it as
 * such: events.py is explicit that "a missing measurement and a zero-latency
 * stage must not look the same on the meter".
 *
 * `endpoint` and `stt` are the framework's own end-of-utterance measurement,
 * populated by `TurnTracker.record_endpointing` (#21). Two properties of them
 * are load-bearing and easy to lose:
 *
 *   - `stt` is a COMPONENT of `endpoint`, taken from the same anchor. The two
 *     must never be added together.
 *   - both are measured BEFORE the tracker's clock starts, so voice-to-voice
 *     first audio is `endpoint + tts_first_audio`, not `tts_first_audio`.
 *
 * They stay null on any turn the voice path did not produce: a typed turn has
 * no endpoint, and the framework returns nothing when its VAD anchors are
 * missing (the agent already reads a flattened 0.0 back as "not measured").
 */
export interface Timings {
  endpoint: number | null
  stt: number | null
  llm_first_sentence: number | null
  guardrail: number | null
  tts_first_audio: number | null
  total: number | null
}

export interface TurnRecord {
  session_id: string
  turn_index: number
  timestamp: string
  buyer_utterance: string
  /** What the model produced, digits intact. */
  generated_sentences: string[]
  /** What was actually handed to TTS, and whether playback completed. */
  spoken_chunks: SpokenChunk[]
  guardrail_decisions: GuardrailViolation[]
  actions: string[]
  timings_ms: Timings
  inventory_version: string
  model: string
  prompt_mode: PromptMode
  guardrail_mode: GuardrailMode
}

// --- event names ----------------------------------------------------------

/**
 * Every event name emitted from `agent/src/adapter/`, read off the two
 * classification tables in events.py (`_REDACTED_FIELDS` and `CLEAR_EVENTS`).
 *
 * docs/02- warns that "the latency meter and any consumer must tolerate new
 * types", which is why `AmbassadorEventName` keeps an open arm: an unknown
 * name is folded as a no-op rather than throwing.
 */
export const KNOWN_EVENT_NAMES = [
  'session_start',
  'session_end',
  'session_error',
  'disclosure',
  'user_turn',
  'endpointing',
  'guardrail',
  'regeneration',
  'bridge',
  'fallback',
  'tool_call',
  'escalation',
  'booking_offered',
  'brief',
  'brief_invalid',
  'brief_error',
  'brief_fallback',
  'brief_retry',
  'brief_stale_dropped',
  'budget_policy',
  'budget_confirmation',
  'budget_confirmation_spoken',
  'budget_settled',
  'lexicon',
  'prohibited_coverage',
  'stt_enabled',
  'stt_disabled',
  'llm_request',
  'llm_upstream_error',
  'llm_ttft',
  'llm_usage',
  'llm_failure',
  'tts_first_audio',
  'tts_connection',
  'tts_pool_reprewarm',
  'interrupted',
  'turn_complete',
  'event_log_backpressure',
] as const

export type KnownEventName = (typeof KNOWN_EVENT_NAMES)[number]

export type AmbassadorEventName = KnownEventName | (string & {})

export function isKnownEventName(name: string): name is KnownEventName {
  return (KNOWN_EVENT_NAMES as readonly string[]).includes(name)
}
