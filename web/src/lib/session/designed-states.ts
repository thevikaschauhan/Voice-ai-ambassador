import type { AuthoredInput } from '@/lib/session/events'
import { initialState, reduceAll } from '@/lib/session/state'
import type { SessionState } from '@/lib/session/state'

/**
 * The designed states, built by folding real events rather than by hand.
 *
 * Issue #9 asks for escalation and failure states designed rather than
 * default. Constructing them from the same reducer the live surface uses is
 * what keeps them honest: a state that cannot be reached by an event sequence
 * is a picture, not a state, and it will drift.
 */
export interface DesignedState {
  id: string
  title: string
  why: string
  state: SessionState
}

function build(inputs: AuthoredInput[], overrides: Partial<SessionState> = {}): SessionState {
  return reduceAll(initialState({ connection: 'live', ...overrides }), inputs)
}

const ESCALATION: AuthoredInput[] = [
  { event: 'user_turn', turn: 1, text: 'And what do the Bugatti Residences go for?' },
  {
    event: 'guardrail',
    turn: 1,
    outcome: 'pass',
    mode: 'enforce',
    ms: 0.24,
    sentence_index: 0,
    raw: 'Bugatti Residences by Binghatti is a branded collection, and pricing there is on enquiry.',
    spoken:
      'Bugatti Residences by Binghatti is a branded collection, and pricing there is on enquiry.',
    validator: null,
    detail: null,
    figures: null,
  },
  {
    event: 'tool_call',
    turn: 1,
    tool: 'escalate_to_human',
    args: { reason: 'branded collection pricing enquiry' },
    at_ms: 1156.3,
    audio_already_played: true,
  },
  {
    event: 'escalation',
    reason: 'branded collection pricing enquiry',
    routed_to: 'human_ambassador',
  },
  {
    event: 'brief',
    turn: 1,
    brief: {
      intent: 'invest',
      budget: { amount: 2000000, currency: 'AED', confirmed: true },
      unit_preference: '1br or 2br',
      timeline: null,
      buyer_location: 'London',
      golden_visa_interest: null,
      hesitations: ['wants branded collection pricing'],
      shortlist_ids: ['binghatti-skyrise'],
      stage: 'escalated',
      language: 'en',
    },
  },
]

const BLOCKED: AuthoredInput[] = [
  { event: 'user_turn', turn: 1, text: 'What does a two-bedroom at Bugatti Residences cost?' },
  {
    event: 'guardrail',
    turn: 1,
    outcome: 'blocked',
    mode: 'enforce',
    ms: 0.35,
    sentence_index: 0,
    raw: 'Bugatti Residences by Binghatti start from around AED 20,000,000 for a two-bedroom.',
    spoken: null,
    validator: 'numeric_claims',
    detail: 'figure 20000000.0 is not in the allowed set for this call',
    figures: [{ surface: 'AED 20,000,000', value: 20000000, kind: 'amount' }],
  },
  {
    event: 'regeneration',
    turn: 1,
    reason: 'numeric_claims: figure 20000000.0 is not in the allowed set',
  },
  {
    event: 'fallback',
    turn: 1,
    text: 'I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.',
    reason: 'guardrail',
  },
]

const WARNED: AuthoredInput[] = [
  { event: 'user_turn', turn: 1, text: 'What does a two-bedroom at Bugatti Residences cost?' },
  {
    event: 'guardrail',
    turn: 1,
    outcome: 'warned',
    mode: 'warn',
    ms: 0.36,
    sentence_index: 0,
    raw: 'Bugatti Residences by Binghatti start from around AED 20,000,000 for a two-bedroom.',
    spoken:
      'Bugatti Residences by Binghatti start from around twenty million dirhams for a two-bedroom.',
    validator: 'numeric_claims',
    detail: 'figure 20000000.0 is not in the allowed set for this call',
    figures: [{ surface: 'AED 20,000,000', value: 20000000, kind: 'amount' }],
  },
]

const BARGE_IN: AuthoredInput[] = [
  { event: 'user_turn', turn: 1, text: 'What would I pay upfront on the Skyrise?' },
  {
    event: 'guardrail',
    turn: 1,
    outcome: 'pass',
    mode: 'enforce',
    ms: 0.3,
    sentence_index: 0,
    raw: 'The booking payment is 20%, which is AED 197,000.',
    spoken:
      'The booking payment is twenty percent, which is one hundred and ninety-seven thousand dirhams.',
    validator: null,
    detail: null,
    figures: null,
  },
  { event: 'interrupted', turn: 1 },
]

const UNRESOLVED: AuthoredInput[] = [
  {
    event: 'brief',
    turn: 1,
    brief: {
      intent: 'invest',
      budget: null,
      unit_preference: null,
      timeline: null,
      buyer_location: null,
      golden_visa_interest: null,
      hesitations: [],
      shortlist_ids: ['binghatti-skyrise', 'binghatti-mirage'],
      stage: 'recommendation',
      language: 'en',
    },
  },
]

export const DESIGNED_STATES: DesignedState[] = [
  {
    id: 'escalation',
    title: 'Escalation',
    why: 'Escalation is a feature, not an error (AGENTS.md invariant 5). A branded price is refused, a human is actually notified, and the ambassador picks the call up already knowing everything the buyer said.',
    state: build(ESCALATION),
  },
  {
    id: 'blocked',
    title: 'Sentence blocked before synthesis',
    why: 'Nothing had been spoken, so the composed fallback became the whole reply. Audio cannot be retracted, which is why the guardrail runs before verbalisation and synthesis rather than after.',
    state: build(BLOCKED),
  },
  {
    id: 'warned',
    title: 'Violation recorded, sentence spoken',
    why: 'GUARDRAIL_MODE=warn. The same fabricated figure, the same validator, and the buyer hears it anyway. This is the state the enforcing mode exists to prevent.',
    state: build(WARNED),
  },
  {
    id: 'barge-in',
    title: 'Barge-in during playback',
    why: 'The buyer talked over the answer. The audit marks the chunk incomplete at chunk granularity; word-level truncation would need TTS word timestamps and is deliberately not claimed.',
    state: build(BARGE_IN),
  },
  {
    id: 'unresolved-shortlist',
    title: 'Shortlist id not in inventory',
    why: 'The agent checks shortlist ids against inventory. An id that does not resolve means that check has a hole, so it is shown rather than quietly dropped.',
    state: build(UNRESOLVED),
  },
  {
    id: 'connection-lost',
    title: 'Connection lost',
    why: 'The room dropped mid-call. The last chunk audits as incomplete rather than as delivered, and the surface says what happened instead of showing an error code.',
    state: build([{ event: 'session_error', error: 'room disconnected' }]),
  },
  {
    id: 'uncertified-language',
    title: 'Opened in English as a fallback',
    why: 'A language with no native-authored disclosure cannot open a call. ALLOW_UNCERTIFIED_LANGUAGE opens in English and marks the stream, which is graceful degradation rather than a quietly shipped language.',
    state: build([
      {
        event: 'disclosure',
        language: 'ar',
        spoken_language: 'en',
        uncertified_fallback: true,
      },
    ]),
  },
  {
    id: 'backpressure',
    title: 'Event log fell behind',
    why: 'The audit never blocks the voice path, so under backpressure the oldest lines lose. The count is emitted once the writer catches up, so a drop is never silent.',
    state: build([{ event: 'event_log_backpressure', dropped: 37, queue_max: 1024 }]),
  },
]
