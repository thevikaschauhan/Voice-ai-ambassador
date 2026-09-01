/**
 * One reducer, two sources.
 *
 * Milestone one drives it from a replay script (`lib/session/replay.ts`).
 * Milestone two drives it from a live LiveKit room plus an events bridge. Both
 * feed THIS function, so the panels never learn which one they are watching -
 * that is the seam, and it is the reason the replay is worth building.
 *
 * The fold mirrors `TurnTracker` in agent/src/adapter/events.py: spoken chunks
 * accumulate in the order they were handed to TTS, barge-in marks the last one
 * incomplete, and a timing that was never observed stays null.
 */

import type { AgentEvent, SessionInput } from '@/lib/session/events'
import { isKnownAgentEvent, isTransportSignal } from '@/lib/session/events'
import type {
  GuardrailMode,
  GuardrailViolation,
  Language,
  LeadBrief,
  PromptMode,
  SpokenChunk,
  Timings,
} from '@/lib/types'

export type ConnectionState = 'idle' | 'connecting' | 'live' | 'ended' | 'lost'

export interface SentenceDecision {
  index: number
  /** What the model produced, digits intact. */
  raw: string
  /** What TTS was handed. Null when the guardrail blocked the sentence. */
  spoken: string | null
  outcome: 'pass' | 'blocked' | 'warned'
  mode: GuardrailMode
  ms: number
  violation: GuardrailViolation | null
}

export interface ComposedSpeech {
  /** Which recovery: they are separate claims, not interchangeable copy. */
  kind: 'bridge' | 'fallback' | 'confirmation'
  text: string
  reason?: string
}

export interface TtsConnection {
  /** false means the buyer waited for a TCP + TLS + WebSocket handshake. */
  reused: boolean
  connectMs: number | null
  pooled: number
}

export interface Usage {
  promptTokens: number | null
  cachedTokens: number | null
  completionTokens: number | null
  reasoningTokens: number | null
  thinkingOff: boolean
}

export interface TurnView {
  turnIndex: number
  buyerUtterance: string
  sentences: SentenceDecision[]
  composed: ComposedSpeech[]
  spokenChunks: SpokenChunk[]
  timings: Timings
  /**
   * Time to first token. Deliberately NOT on `timings`: `Timings` mirrors the
   * Pydantic model in schemas.py, which has no such field, and the mark lives
   * on the `llm_ttft` and `turn_complete` events instead. Keeping it beside
   * the mirror rather than inside it stops the two drifting.
   */
  ttftMs: number | null
  /**
   * `endpoint - stt`: what the turn detector spent waiting once the words were
   * already in hand, and the only part of the endpointing budget that
   * optimising the detector could recover. Not a stage of its own.
   */
  afterTranscriptMs: number | null
  ttsConnection: TtsConnection | null
  actions: string[]
  regenerated: boolean
  interrupted: boolean
  auditIncomplete: boolean
  usage: Usage | null
  complete: boolean
  /**
   * The deterministic budget policy took the turn, so the model never ran
   * (ADR-011). The meter must not read the missing LLM timing as a fast turn.
   */
  policyTurn: boolean
}

export interface SessionState {
  sessionId: string | null
  connection: ConnectionState
  language: Language
  spokenLanguage: Language | null
  uncertifiedFallback: boolean
  promptMode: PromptMode
  guardrailMode: GuardrailMode
  model: string | null
  inventoryVersion: string | null
  disclosure: string | null
  turns: TurnView[]
  brief: LeadBrief | null
  /**
   * The deterministic policy's verdict (ADR-011), held apart from the brief
   * extractor's model-inferred `budget.confirmed` on purpose: docs/04- is
   * explicit that the two are different sources and the policy wins wherever
   * they disagree. Kept on the session rather than folded into the brief so a
   * later brief cannot quietly un-settle a settled currency.
   */
  budgetSettled: { turn: number; currency: string } | null
  escalation: { reason: string; routedTo: string } | null
  booking: { slot: string; turn: number } | null
  buyerSpeaking: boolean
  agentSpeaking: boolean
  /** Buyer audio arrived while the agent was still speaking. */
  bargeIn: boolean
  levels: number[]
  /** Where the waveform's numbers come from, or that there are none. */
  audioSource: 'none' | 'room'
  error: string | null
  droppedEvents: number
}

const LEVEL_WINDOW = 48

export function initialState(overrides: Partial<SessionState> = {}): SessionState {
  return {
    sessionId: null,
    connection: 'idle',
    language: 'en',
    spokenLanguage: null,
    uncertifiedFallback: false,
    promptMode: 'ambassador',
    guardrailMode: 'enforce',
    model: null,
    inventoryVersion: null,
    disclosure: null,
    turns: [],
    brief: null,
    budgetSettled: null,
    escalation: null,
    booking: null,
    buyerSpeaking: false,
    agentSpeaking: false,
    bargeIn: false,
    levels: [],
    audioSource: 'none',
    error: null,
    droppedEvents: 0,
    ...overrides,
  }
}

function emptyTimings(): Timings {
  return {
    endpoint: null,
    stt: null,
    llm_first_sentence: null,
    guardrail: null,
    tts_first_audio: null,
    total: null,
  }
}

function newTurn(turnIndex: number): TurnView {
  return {
    turnIndex,
    buyerUtterance: '',
    sentences: [],
    composed: [],
    spokenChunks: [],
    timings: emptyTimings(),
    ttftMs: null,
    afterTranscriptMs: null,
    ttsConnection: null,
    actions: [],
    regenerated: false,
    interrupted: false,
    auditIncomplete: false,
    usage: null,
    complete: false,
    policyTurn: false,
  }
}

/** Copy-on-write access to one turn, creating it if the event arrived first. */
function withTurn(
  state: SessionState,
  turnIndex: number,
  mutate: (turn: TurnView) => void,
): SessionState {
  const turns = [...state.turns]
  const at = turns.findIndex((t) => t.turnIndex === turnIndex)
  const turn: TurnView = at === -1 ? newTurn(turnIndex) : { ...turns[at] }
  mutate(turn)
  if (at === -1) {
    turns.push(turn)
    turns.sort((a, b) => a.turnIndex - b.turnIndex)
  } else {
    turns[at] = turn
  }
  return { ...state, turns }
}

function speak(turn: TurnView, text: string): void {
  turn.spokenChunks = [...turn.spokenChunks, { text, completed: true }]
}

function reduceAgentEvent(state: SessionState, event: AgentEvent): SessionState {
  switch (event.event) {
    case 'session_start':
      return {
        ...state,
        sessionId: event.session ?? state.sessionId,
        connection: 'live',
        language: event.language,
        promptMode: event.prompt_mode,
        guardrailMode: event.guardrail_mode,
        model: event.model,
        inventoryVersion: event.inventory_version,
      }

    case 'session_end':
      return { ...state, connection: 'ended', agentSpeaking: false, buyerSpeaking: false }

    case 'session_error':
      return { ...state, connection: 'lost', error: event.error }

    case 'disclosure':
      return {
        ...state,
        spokenLanguage: event.spoken_language,
        uncertifiedFallback: event.uncertified_fallback,
        disclosure: event.text ?? state.disclosure,
      }

    case 'user_turn':
      return withTurn(state, event.turn, (turn) => {
        turn.buyerUtterance = event.text
      })

    case 'guardrail':
      return withTurn(state, event.turn, (turn) => {
        turn.sentences = [
          ...turn.sentences,
          {
            index: event.sentence_index,
            raw: event.raw,
            spoken: event.spoken,
            outcome: event.outcome,
            mode: event.mode,
            ms: event.ms,
            violation:
              event.validator === null
                ? null
                : {
                    validator: event.validator,
                    detail: event.detail ?? '',
                    figures: event.figures ?? [],
                  },
          },
        ]
        // Mirrors TurnTracker.record_guardrail: guardrail_ms accumulates
        // across the turn's sentences, and stays null until one has run.
        turn.timings = {
          ...turn.timings,
          guardrail: round2((turn.timings.guardrail ?? 0) + event.ms),
        }
        if (event.spoken !== null) speak(turn, event.spoken)
      })

    case 'endpointing':
      return withTurn(state, event.turn, (turn) => {
        // stt is a COMPONENT of endpoint, taken from the same anchor, so the
        // two are stored side by side and never summed.
        turn.timings = {
          ...turn.timings,
          endpoint: event.endpoint_ms,
          stt: event.stt_ms,
        }
        turn.afterTranscriptMs = event.after_transcript_ms
      })

    case 'tts_connection':
      if (event.turn === null) return state
      return withTurn(state, event.turn, (turn) => {
        turn.ttsConnection = {
          reused: event.reused,
          connectMs: event.connect_ms,
          pooled: event.pooled,
        }
      })

    case 'llm_ttft':
      return withTurn(state, event.turn, (turn) => {
        turn.ttftMs = event.ms
      })

    case 'regeneration':
      return withTurn(state, event.turn, (turn) => {
        turn.regenerated = true
      })

    case 'bridge':
      return withTurn(state, event.turn, (turn) => {
        turn.composed = [...turn.composed, { kind: 'bridge', text: event.text }]
        speak(turn, event.text)
      })

    case 'fallback':
      return withTurn(state, event.turn, (turn) => {
        turn.composed = [
          ...turn.composed,
          { kind: 'fallback', text: event.text, reason: event.reason },
        ]
        speak(turn, event.text)
      })

    case 'budget_confirmation_spoken':
      return withTurn(state, event.turn, (turn) => {
        turn.policyTurn = true
        if (event.text === undefined) return
        turn.composed = [
          ...turn.composed,
          { kind: 'confirmation', text: event.text, reason: event.action },
        ]
        speak(turn, event.text)
      })

    case 'budget_settled':
      return { ...state, budgetSettled: { turn: event.turn, currency: event.currency } }

    case 'tool_call':
      return withTurn(state, event.turn, (turn) => {
        turn.actions = [...turn.actions, event.tool]
      })

    case 'escalation':
      return {
        ...state,
        escalation: { reason: event.reason, routedTo: event.routed_to },
        brief: state.brief ? { ...state.brief, stage: 'escalated' } : state.brief,
      }

    case 'booking_offered':
      return { ...state, booking: { slot: event.slot, turn: event.turn } }

    case 'brief':
      return { ...state, brief: event.brief }

    case 'llm_usage':
      return withTurn(state, event.turn, (turn) => {
        turn.usage = {
          promptTokens: event.prompt_tokens,
          cachedTokens: event.cached_tokens,
          completionTokens: event.completion_tokens,
          reasoningTokens: event.reasoning_tokens,
          thinkingOff: event.thinking_off,
        }
      })

    case 'llm_failure':
      return withTurn(state, event.turn, (turn) => {
        turn.actions = [...turn.actions, 'llm_failure']
      })

    case 'interrupted':
      return withTurn(state, event.turn, (turn) => {
        turn.interrupted = true
        // Chunk granularity is the claim (docs/04-): the last chunk handed to
        // TTS may not have finished playing.
        if (turn.spokenChunks.length > 0) {
          const chunks = [...turn.spokenChunks]
          chunks[chunks.length - 1] = {
            ...chunks[chunks.length - 1],
            completed: false,
          }
          turn.spokenChunks = chunks
        }
      })

    case 'turn_complete':
      return withTurn(state, event.turn, (turn) => {
        turn.complete = true
        turn.regenerated = turn.regenerated || event.regenerated
        turn.auditIncomplete = event.audit_incomplete
        turn.actions = event.actions.length > 0 ? event.actions : turn.actions
        turn.ttftMs = event.llm_ttft_ms
        turn.timings = {
          // `endpointing` normally arrives first and carries the same numbers;
          // taking the sealed record's view keeps the two consistent, and a
          // turn with no end-of-utterance (a typed one) stays null rather than
          // showing a zero.
          endpoint: event.endpoint_ms,
          stt: event.stt_ms,
          llm_first_sentence: event.llm_first_sentence_ms,
          guardrail: event.guardrail_ms,
          tts_first_audio: event.tts_first_audio_ms,
          total: event.total_ms,
        }
      })

    case 'event_log_backpressure':
      return { ...state, droppedEvents: state.droppedEvents + event.dropped }

    default:
      return state
  }
}

export function reduce(state: SessionState, input: SessionInput): SessionState {
  if (!isTransportSignal(input)) {
    // docs/02-: a consumer must tolerate event types it has never seen.
    return isKnownAgentEvent(input) ? reduceAgentEvent(state, input) : state
  }

  switch (input.signal) {
    case 'connection':
      return { ...state, connection: input.state }
    case 'buyer_speaking':
      return {
        ...state,
        buyerSpeaking: input.on,
        bargeIn: input.on ? state.agentSpeaking : false,
      }
    case 'agent_speaking':
      return {
        ...state,
        agentSpeaking: input.on,
        bargeIn: input.on ? state.bargeIn : false,
      }
    case 'audio_source':
      // Losing the room clears the trace: stale bars under a "no audio"
      // label would be the flat-line lie in a different shape.
      return {
        ...state,
        audioSource: input.kind,
        levels: input.kind === 'none' ? [] : state.levels,
      }
    case 'level': {
      const levels = [...state.levels, input.value]
      return {
        ...state,
        levels: levels.length > LEVEL_WINDOW ? levels.slice(-LEVEL_WINDOW) : levels,
      }
    }
  }
}

export function reduceAll(state: SessionState, inputs: readonly SessionInput[]): SessionState {
  return inputs.reduce(reduce, state)
}

function round2(value: number): number {
  return Math.round(value * 100) / 100
}
