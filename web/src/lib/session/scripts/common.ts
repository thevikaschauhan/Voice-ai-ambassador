/**
 * Shared pieces of the replay fixtures.
 *
 * EVERY figure spoken in these scripts is either a record in
 * `data/inventory.json`, a derivation computed from one (a milestone
 * percentage of a listed price), or the buyer's own stated budget read back to
 * them. AGENTS.md invariant 1 applies to fixtures as much as to the model:
 * nothing here is invented inventory. The deliberately-fabricated figures in
 * the naive scripts are marked as such and are, by construction, absent from
 * the allowed set.
 *
 * Timings are the measured figures from ADR-016/ADR-017 (LLM time to first
 * token 685ms with caching live, Deepgram nova-3 258-327ms after audio ends,
 * Fish first audio in the 75-300ms band), not aspirational ones. `endpoint`
 * and `stt` are absent from `turn_complete` because `TurnTracker.finish()`
 * does not populate them - the meter says "not measured" and means it.
 */

import type { SessionInput } from '@/lib/session/events'
import type { LeadBrief } from '@/lib/types'
import type { ReplayStep } from './types'

export const MODEL = 'qwen/qwen3.7-flash'
export const INVENTORY_VERSION = 'inventory.json@VERIFY-placeholder'

/** Build steps from `[delayMs, input]` pairs. */
export function steps(...pairs: [number, SessionInput][]): ReplayStep[] {
  return pairs.map(([after, input]) => ({ after, input }))
}

export function sessionStart(
  promptMode: 'ambassador' | 'naive',
  guardrailMode: 'enforce' | 'warn',
): SessionInput {
  return {
    event: 'session_start',
    session: `replay-${promptMode}-${guardrailMode}`,
    model: MODEL,
    language: 'en',
    prompt_mode: promptMode,
    guardrail_mode: guardrailMode,
    inventory_version: INVENTORY_VERSION,
  }
}

/** Fixed copy from data/disclosures.yaml, never model-generated (ADR-013). */
export const DISCLOSURE: SessionInput = {
  event: 'disclosure',
  language: 'en',
  spoken_language: 'en',
  uncertified_fallback: false,
  text:
    'You are speaking with an AI ambassador for Binghatti. This call is transcribed, not recorded.',
}

export function usage(turn: number, completion: number): SessionInput {
  return {
    event: 'llm_usage',
    turn,
    model: MODEL,
    prompt_tokens: 1580,
    // ADR-016: structurally zero on the voice path until the plugin can emit
    // content blocks. The meter plumbs it so it shows the day that changes.
    cached_tokens: 0,
    completion_tokens: completion,
    reasoning_tokens: 0,
    thinking_off: true,
  }
}

export function brief(turn: number, patch: Partial<LeadBrief>): SessionInput {
  return {
    event: 'brief',
    turn,
    brief: {
      intent: 'unknown',
      budget: null,
      unit_preference: null,
      timeline: null,
      buyer_location: null,
      golden_visa_interest: null,
      hesitations: [],
      shortlist_ids: [],
      stage: 'opening',
      language: 'en',
      ...patch,
    },
  }
}

export function agentSpeaks(on: boolean): SessionInput {
  return { signal: 'agent_speaking', on }
}

export function buyerSpeaks(on: boolean): SessionInput {
  return { signal: 'buyer_speaking', on }
}

/** A short burst of waveform levels, deterministic so replays are repeatable. */
export function levels(count: number, seed: number): [number, SessionInput][] {
  const out: [number, SessionInput][] = []
  let x = seed
  for (let i = 0; i < count; i += 1) {
    x = (x * 1103515245 + 12345) % 2147483648
    out.push([60, { signal: 'level', value: 0.18 + (x / 2147483648) * 0.72 }])
  }
  return out
}
