/**
 * The event union the demo surface folds into state.
 *
 * Field names are copied from the `emit(...)` call sites in
 * `agent/src/adapter/events.py`. Where that file redacts a field on the way to
 * stdout, this union keeps it, because the surface reads the in-process
 * records (issue #9) - see the fidelity note in `lib/types.ts`.
 *
 * Transport signals (who is speaking, is the room connected) are NOT agent
 * events: they come from the LiveKit room in milestone two. They live in their
 * own union below so this one stays a faithful mirror of events.py.
 */

import { isKnownEventName } from '@/lib/types'
import type {
  ExtractedFigure,
  GuardrailMode,
  Language,
  LeadBrief,
  PromptMode,
  ValidatorName,
} from '@/lib/types'

interface Base {
  /** ISO-8601 with milliseconds, as `_now_iso()` writes it. */
  ts?: string
  session?: string
}

export type AgentEvent = Base &
  (
    | {
        event: 'session_start'
        model: string
        language: Language
        prompt_mode: PromptMode
        guardrail_mode: GuardrailMode
        inventory_version: string
      }
    | { event: 'session_end'; turns: number }
    | { event: 'session_error'; error: string }
    | {
        event: 'disclosure'
        language: Language
        spoken_language: Language
        uncertified_fallback: boolean
        /** In-process only: the fixed copy from data/disclosures.yaml. */
        text?: string
      }
    | { event: 'user_turn'; turn: number; text: string }
    /**
     * The framework's own end-of-utterance measurement (#21). `stt_ms` is a
     * component of `endpoint_ms`, and `after_transcript_ms` is the difference:
     * what the turn detector spent waiting once the words were in hand.
     */
    | {
        event: 'endpointing'
        turn: number
        endpoint_ms: number | null
        stt_ms: number | null
        after_transcript_ms: number | null
        turn_committed_ms: number | null
      }
    | {
        event: 'guardrail'
        turn: number
        outcome: 'pass' | 'blocked' | 'warned'
        mode: GuardrailMode
        ms: number
        sentence_index: number
        raw: string
        spoken: string | null
        validator: ValidatorName | null
        detail: string | null
        figures: ExtractedFigure[] | null
      }
    | { event: 'regeneration'; turn: number; reason: string }
    | { event: 'bridge'; turn: number; text: string }
    | {
        event: 'fallback'
        turn: number
        text: string
        reason: string
      }
    | {
        event: 'tool_call'
        turn: number
        tool: string
        args: Record<string, unknown>
        at_ms: number
        audio_already_played: boolean
      }
    | { event: 'escalation'; reason: string; routed_to: string }
    | { event: 'booking_offered'; turn: number; slot: string }
    | { event: 'brief'; turn: number; brief: LeadBrief }
    | { event: 'brief_invalid'; turn: number; error: string }
    | {
        event: 'budget_confirmation'
        turn: number
        action: 'ask_currency' | 'read_back' | 'settle' | 'give_up' | 'cannot_convert'
        currency: string | null
        attempts: number
      }
    | {
        event: 'budget_confirmation_spoken'
        turn: number
        action: string
        /** In-process only: the echo never reaches the emitted stream. */
        text?: string
      }
    | { event: 'budget_settled'; turn: number; currency: string }
    | { event: 'llm_ttft'; turn: number; ms: number; model: string }
    | {
        event: 'llm_usage'
        turn: number
        model: string
        prompt_tokens: number | null
        cached_tokens: number | null
        completion_tokens: number | null
        reasoning_tokens: number | null
        thinking_off: boolean
      }
    | { event: 'llm_failure'; turn: number; error: string; detail?: string }
    | {
        event: 'tts_first_audio'
        turn: number
        ms: number
        since_first_sentence_ms: number | null
      }
    /** Did this turn's audio come off a pooled socket, or wait for a handshake? */
    | {
        event: 'tts_connection'
        turn: number | null
        reused: boolean
        connect_ms: number | null
        pooled: number
      }
    | { event: 'tts_pool_reprewarm'; turn: number; outcome: string }
    | { event: 'interrupted'; turn: number }
    | {
        event: 'turn_complete'
        turn: number
        endpoint_ms: number | null
        stt_ms: number | null
        llm_ttft_ms: number | null
        llm_first_sentence_ms: number | null
        guardrail_ms: number | null
        tts_first_audio_ms: number | null
        total_ms: number | null
        sentences: number
        violations: number
        regenerated: boolean
        actions: string[]
        reasoning_tokens: number | null
        audit_incomplete: boolean
      }
    | { event: 'event_log_backpressure'; dropped: number; queue_max: number }
  )

/**
 * docs/02-: "the latency meter and any consumer must tolerate new types".
 *
 * The tolerance is a runtime guard rather than an open arm on the union: an
 * open arm would collapse the discriminant and every `case` would stop
 * narrowing, which is how a consumer ends up reading a field off an event that
 * does not have one. An unrecognised name folds as a no-op instead.
 */
export type UnknownAgentEvent = Base & { event: string }

export type AnyAgentEvent = AgentEvent | UnknownAgentEvent

export function isKnownAgentEvent(event: AnyAgentEvent): event is AgentEvent {
  return isKnownEventName(event.event)
}

/** Room/transport state. LiveKit's, not the agent's. */
export type TransportSignal =
  | { signal: 'connection'; state: 'idle' | 'connecting' | 'live' | 'ended' | 'lost' }
  | { signal: 'buyer_speaking'; on: boolean }
  | { signal: 'agent_speaking'; on: boolean }
  /** Normalised 0-1 levels for the waveform, newest last. */
  | { signal: 'level'; value: number }
  /**
   * Whether real audio is attached. `none` is not a failure: it is the
   * ordinary state when the surface is watching the event stream without a
   * room, and it is what keeps the waveform saying "no audio track attached"
   * instead of drawing a flat trace that would read as silence.
   */
  | { signal: 'audio_source'; kind: 'none' | 'room' }

/**
 * What arrives from a source. The unknown arm belongs HERE and only here: it
 * describes what a bridge may hand us, not what we author.
 */
export type SessionInput = AnyAgentEvent | TransportSignal

/**
 * What this repository writes: replay fixtures, designed states, the text-mode
 * core. Closed on purpose - typing authored events as `SessionInput` lets a
 * fixture with a missing or misspelled field match the unknown arm, type
 * clean, and then fold as a no-op at runtime, which is a silently dead fixture.
 */
export type AuthoredInput = AgentEvent | TransportSignal

export function isTransportSignal(input: SessionInput): input is TransportSignal {
  return 'signal' in input
}
