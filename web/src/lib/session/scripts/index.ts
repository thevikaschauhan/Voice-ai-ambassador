/**
 * Four replay fixtures, one per position of the toggle pair.
 *
 * docs/03- prescribes exactly two of the four for the stage, and says why the
 * other two are weaker - so all four are recorded rather than two, and the
 * surface tells the truth about what each one demonstrates. A toggle that
 * lands on an unrecorded state is a hole someone will click into on the day.
 */

import type { SessionInput } from '@/lib/session/events'
import {
  DISCLOSURE,
  agentSpeaks,
  brief,
  buyerSpeaks,
  levels,
  sessionStart,
  steps,
  usage,
} from './common'
import type { ReplayScript } from './types'
import { modeKey } from './types'

const BUDGET = { amount: 2000000, currency: 'AED', confirmed: true }

// --- ambassador + enforce: the demo path (docs/07-) ------------------------

const ambassadorEnforce: ReplayScript = {
  id: 'ambassador-enforce',
  promptMode: 'ambassador',
  guardrailMode: 'enforce',
  label: 'Ambassador prompt, guardrail enforcing',
  note: 'The shipped configuration. A blocked sentence is regenerated, and a branded price is refused rather than guessed.',
  steps: steps(
    [200, sessionStart('ambassador', 'enforce')],
    [150, DISCLOSURE],
    [400, agentSpeaks(true)],
    ...levels(10, 7),
    [200, agentSpeaks(false)],

    // Turn 1. The budget policy takes the turn: the model never runs, so the
    // LLM and guardrail stages have nothing to measure (ADR-011).
    [700, buyerSpeaks(true)],
    ...levels(12, 21),
    [
      120,
      {
        event: 'user_turn',
        turn: 1,
        text: 'I am looking to invest in Dubai. My budget is around two million.',
      },
    ],
    [80, buyerSpeaks(false)],
    [
      140,
      {
        event: 'budget_confirmation',
        turn: 1,
        action: 'ask_currency',
        currency: null,
        attempts: 1,
      },
    ],
    [70, agentSpeaks(true)],
    [
      0,
      {
        event: 'budget_confirmation_spoken',
        turn: 1,
        action: 'ask_currency',
        text: 'Two million - is that in dirhams or in rupees?',
      },
    ],
    ...levels(8, 33),
    [
      120,
      {
        event: 'turn_complete',
        turn: 1,
        llm_ttft_ms: null,
        llm_first_sentence_ms: null,
        guardrail_ms: null,
        tts_first_audio_ms: 214.0,
        total_ms: 268.5,
        sentences: 0,
        violations: 0,
        regenerated: false,
        actions: [],
        reasoning_tokens: null,
        audit_incomplete: false,
      },
    ],
    [100, agentSpeaks(false)],

    // Turn 2. Settled, then a recommendation - with one blocked sentence and
    // the single repair retry the policy allows.
    [900, buyerSpeaks(true)],
    ...levels(6, 51),
    [100, { event: 'user_turn', turn: 2, text: 'In dirhams.' }],
    [60, buyerSpeaks(false)],
    [90, { event: 'budget_settled', turn: 2, currency: 'AED' }],
    [
      690,
      { event: 'llm_ttft', turn: 2, ms: 688.4, model: 'qwen/qwen3.7-flash' },
    ],
    [
      250,
      {
        event: 'guardrail',
        turn: 2,
        outcome: 'blocked',
        mode: 'enforce',
        ms: 0.34,
        sentence_index: 0,
        raw: 'Binghatti Skyrise in Business Bay starts from AED 950,000.',
        spoken: null,
        validator: 'numeric_claims',
        detail: 'figure 950000.0 is not in the allowed set for this call',
        figures: [{ surface: 'AED 950,000', value: 950000, kind: 'amount' }],
      },
    ],
    [
      10,
      {
        event: 'regeneration',
        turn: 2,
        reason: 'numeric_claims: figure 950000.0 is not in the allowed set',
      },
    ],
    [
      520,
      {
        event: 'guardrail',
        turn: 2,
        outcome: 'pass',
        mode: 'enforce',
        ms: 0.31,
        sentence_index: 1,
        raw: 'Binghatti Skyrise in Business Bay starts from AED 985,000.',
        spoken:
          'Binghatti Skyrise in Business Bay starts from nine hundred and eighty-five thousand dirhams.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [40, agentSpeaks(true)],
    ...levels(10, 67),
    [
      210,
      {
        event: 'guardrail',
        turn: 2,
        outcome: 'pass',
        mode: 'enforce',
        ms: 0.29,
        sentence_index: 2,
        raw: 'Units run from 420 to 1,200 square feet, with handover in Q4 2026.',
        spoken:
          'Units run from four hundred and twenty to one thousand two hundred square feet, with handover in the fourth quarter of 2026.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [
      190,
      {
        event: 'guardrail',
        turn: 2,
        outcome: 'pass',
        mode: 'enforce',
        ms: 0.27,
        sentence_index: 3,
        raw: 'If you want a lower entry, Binghatti Circle in Jumeirah Village Circle starts from AED 650,000.',
        spoken:
          'If you want a lower entry, Binghatti Circle in Jumeirah Village Circle starts from six hundred and fifty thousand dirhams.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [
      30,
      {
        event: 'tts_first_audio',
        turn: 2,
        ms: 1078.6,
        since_first_sentence_ms: 138.9,
      },
    ],
    [40, usage(2, 74)],
    ...levels(14, 83),
    [
      120,
      brief(2, {
        intent: 'invest',
        budget: BUDGET,
        stage: 'recommendation',
        shortlist_ids: ['binghatti-skyrise', 'binghatti-circle'],
      }),
    ],
    [
      60,
      {
        event: 'turn_complete',
        turn: 2,
        llm_ttft_ms: 688.4,
        llm_first_sentence_ms: 939.7,
        guardrail_ms: 1.21,
        tts_first_audio_ms: 1078.6,
        total_ms: 1462.3,
        sentences: 4,
        violations: 1,
        regenerated: true,
        actions: [],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],
    [150, agentSpeaks(false)],

    // Turn 3. The payment question, answered from a derived figure - and the
    // buyer talks over the answer, so the last chunk audits incomplete.
    [800, buyerSpeaks(true)],
    ...levels(9, 97),
    [
      110,
      {
        event: 'user_turn',
        turn: 3,
        text: 'What would I pay upfront on the Skyrise?',
      },
    ],
    [70, buyerSpeaks(false)],
    [
      640,
      { event: 'llm_ttft', turn: 3, ms: 641.2, model: 'qwen/qwen3.7-flash' },
    ],
    [
      260,
      {
        event: 'guardrail',
        turn: 3,
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
    ],
    [30, agentSpeaks(true)],
    [
      20,
      {
        event: 'tts_first_audio',
        turn: 3,
        ms: 952.4,
        since_first_sentence_ms: 111.2,
      },
    ],
    ...levels(8, 113),
    [
      200,
      {
        event: 'guardrail',
        turn: 3,
        outcome: 'pass',
        mode: 'enforce',
        ms: 0.26,
        sentence_index: 1,
        raw: 'After that it is 50% across construction and 30% on handover.',
        spoken:
          'After that it is fifty percent across construction and thirty percent on handover.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [40, usage(3, 41)],
    [180, buyerSpeaks(true)],
    [20, { event: 'interrupted', turn: 3 }],
    [60, agentSpeaks(false)],
    [
      80,
      {
        event: 'turn_complete',
        turn: 3,
        llm_ttft_ms: 641.2,
        llm_first_sentence_ms: 901.6,
        guardrail_ms: 0.56,
        tts_first_audio_ms: 952.4,
        total_ms: 1287.9,
        sentences: 2,
        violations: 0,
        regenerated: false,
        actions: [],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],

    // Turn 4. Branded pricing: no figure, no range, and a human is actually
    // notified rather than merely mentioned.
    ...levels(10, 131),
    [
      120,
      {
        event: 'user_turn',
        turn: 4,
        text: 'And what do the Bugatti Residences go for?',
      },
    ],
    [80, buyerSpeaks(false)],
    [
      700,
      { event: 'llm_ttft', turn: 4, ms: 703.8, model: 'qwen/qwen3.7-flash' },
    ],
    [
      240,
      {
        event: 'guardrail',
        turn: 4,
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
    ],
    [30, agentSpeaks(true)],
    [
      20,
      {
        event: 'tts_first_audio',
        turn: 4,
        ms: 1011.5,
        since_first_sentence_ms: 104.7,
      },
    ],
    ...levels(6, 149),
    [
      140,
      {
        event: 'tool_call',
        turn: 4,
        tool: 'escalate_to_human',
        args: { reason: 'branded collection pricing enquiry' },
        at_ms: 1156.3,
        audio_already_played: true,
      },
    ],
    [
      10,
      {
        event: 'escalation',
        reason: 'branded collection pricing enquiry',
        routed_to: 'human_ambassador',
      },
    ],
    [
      200,
      {
        event: 'guardrail',
        turn: 4,
        outcome: 'pass',
        mode: 'enforce',
        ms: 0.22,
        sentence_index: 1,
        raw: 'I have asked one of our ambassadors to come back to you on it directly.',
        spoken:
          'I have asked one of our ambassadors to come back to you on it directly.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [40, usage(4, 38)],
    ...levels(8, 167),
    [
      100,
      brief(4, {
        intent: 'invest',
        budget: BUDGET,
        unit_preference: '1br or 2br',
        buyer_location: 'London',
        stage: 'escalated',
        shortlist_ids: ['binghatti-skyrise', 'binghatti-circle'],
        hesitations: ['wants branded collection pricing'],
      }),
    ],
    [
      60,
      {
        event: 'turn_complete',
        turn: 4,
        llm_ttft_ms: 703.8,
        llm_first_sentence_ms: 946.1,
        guardrail_ms: 0.46,
        tts_first_audio_ms: 1011.5,
        total_ms: 1594.2,
        sentences: 2,
        violations: 0,
        regenerated: false,
        actions: ['escalate_to_human'],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],
    [200, agentSpeaks(false)],
  ),
}

// --- naive + warn: the trap (docs/03-) ------------------------------------
//
// The fabricated figures below are deliberately absent from the allowed set,
// which is what makes the validator object to them. No guaranteed-return
// language appears in either naive script: AGENTS.md forbids writing it into
// fixtures at all, and the numeric fabrication is the centrepiece anyway.

const NAIVE_QUESTION: SessionInput = {
  event: 'user_turn',
  turn: 1,
  text: 'What does a two-bedroom at Bugatti Residences cost?',
}

const naiveWarn: ReplayScript = {
  id: 'naive-warn',
  promptMode: 'naive',
  guardrailMode: 'warn',
  label: 'Typical chatbot configuration',
  note: 'A generic assistant prompt with the validator observing but not blocking. The fabricated figure is recorded, and the buyer hears it anyway.',
  steps: steps(
    [200, sessionStart('naive', 'warn')],
    [150, DISCLOSURE],
    [600, buyerSpeaks(true)],
    ...levels(12, 11),
    [120, NAIVE_QUESTION],
    [70, buyerSpeaks(false)],
    [
      660,
      { event: 'llm_ttft', turn: 1, ms: 662.9, model: 'qwen/qwen3.7-flash' },
    ],
    [
      250,
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
        figures: [
          { surface: 'AED 20,000,000', value: 20000000, kind: 'amount' },
        ],
      },
    ],
    [40, agentSpeaks(true)],
    [
      20,
      {
        event: 'tts_first_audio',
        turn: 1,
        ms: 1034.7,
        since_first_sentence_ms: 121.4,
      },
    ],
    ...levels(12, 29),
    [
      210,
      {
        event: 'guardrail',
        turn: 1,
        outcome: 'warned',
        mode: 'warn',
        ms: 0.31,
        sentence_index: 1,
        raw: 'Those units are typically around 2,400 square feet.',
        spoken: 'Those units are typically around two thousand four hundred square feet.',
        validator: 'numeric_claims',
        detail: 'figure 2400.0 is not in the allowed set for this call',
        figures: [{ surface: '2,400', value: 2400, kind: 'amount' }],
      },
    ],
    [40, usage(1, 52)],
    ...levels(8, 43),
    [
      120,
      {
        event: 'turn_complete',
        turn: 1,
        llm_ttft_ms: 662.9,
        llm_first_sentence_ms: 913.3,
        guardrail_ms: 0.67,
        tts_first_audio_ms: 1034.7,
        total_ms: 1401.8,
        sentences: 2,
        violations: 2,
        regenerated: false,
        actions: [],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],
    [200, agentSpeaks(false)],
  ),
}

// --- naive + enforce: the same model, stopped ----------------------------

const naiveEnforce: ReplayScript = {
  id: 'naive-enforce',
  promptMode: 'naive',
  guardrailMode: 'enforce',
  label: 'Generic prompt, guardrail enforcing',
  note: 'The same generic assistant, with the validator blocking. Nothing had been spoken, so the composed fallback becomes the whole reply and a human is notified.',
  steps: steps(
    [200, sessionStart('naive', 'enforce')],
    [150, DISCLOSURE],
    [600, buyerSpeaks(true)],
    ...levels(12, 11),
    [120, NAIVE_QUESTION],
    [70, buyerSpeaks(false)],
    [
      670,
      { event: 'llm_ttft', turn: 1, ms: 671.5, model: 'qwen/qwen3.7-flash' },
    ],
    [
      250,
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
        figures: [
          { surface: 'AED 20,000,000', value: 20000000, kind: 'amount' },
        ],
      },
    ],
    [
      10,
      {
        event: 'regeneration',
        turn: 1,
        reason: 'numeric_claims: figure 20000000.0 is not in the allowed set',
      },
    ],
    [
      540,
      {
        event: 'guardrail',
        turn: 1,
        outcome: 'blocked',
        mode: 'enforce',
        ms: 0.33,
        sentence_index: 1,
        raw: 'Two-bedrooms in that collection are usually priced above AED 18,000,000.',
        spoken: null,
        validator: 'numeric_claims',
        detail: 'figure 18000000.0 is not in the allowed set for this call',
        figures: [
          { surface: 'AED 18,000,000', value: 18000000, kind: 'amount' },
        ],
      },
    ],
    [
      30,
      {
        event: 'fallback',
        turn: 1,
        text: 'I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.',
        reason: 'guardrail',
      },
    ],
    [30, agentSpeaks(true)],
    [
      20,
      {
        event: 'tts_first_audio',
        turn: 1,
        ms: 1298.2,
        since_first_sentence_ms: 96.4,
      },
    ],
    [
      40,
      {
        event: 'tool_call',
        turn: 1,
        tool: 'escalate_to_human',
        args: { reason: 'guardrail fallback after repair retry' },
        at_ms: 1342.7,
        audio_already_played: true,
      },
    ],
    [
      10,
      {
        event: 'escalation',
        reason: 'guardrail fallback after repair retry',
        routed_to: 'human_ambassador',
      },
    ],
    [40, usage(1, 61)],
    ...levels(10, 59),
    [
      120,
      brief(1, { intent: 'unknown', stage: 'escalated' }),
    ],
    [
      60,
      {
        event: 'turn_complete',
        turn: 1,
        llm_ttft_ms: 671.5,
        llm_first_sentence_ms: 921.8,
        guardrail_ms: 0.68,
        tts_first_audio_ms: 1298.2,
        total_ms: 1706.4,
        sentences: 2,
        violations: 2,
        regenerated: true,
        actions: ['escalate_to_human'],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],
    [200, agentSpeaks(false)],
  ),
}

// --- ambassador + warn: the honest null result (docs/03-) -----------------

const ambassadorWarn: ReplayScript = {
  id: 'ambassador-warn',
  promptMode: 'ambassador',
  guardrailMode: 'warn',
  label: 'Ambassador prompt, guardrail observing only',
  note: 'docs/03- predicts this pairing underwhelms: the prompt still instructs strict grounding, so the model refuses on its own and the toggle appears to do nothing. That is the point - a prompt reduces violations, it does not eliminate them.',
  steps: steps(
    [200, sessionStart('ambassador', 'warn')],
    [150, DISCLOSURE],
    [600, buyerSpeaks(true)],
    ...levels(12, 11),
    [120, NAIVE_QUESTION],
    [70, buyerSpeaks(false)],
    [
      680,
      { event: 'llm_ttft', turn: 1, ms: 679.3, model: 'qwen/qwen3.7-flash' },
    ],
    [
      250,
      {
        event: 'guardrail',
        turn: 1,
        outcome: 'pass',
        mode: 'warn',
        ms: 0.25,
        sentence_index: 0,
        raw: 'Bugatti Residences by Binghatti is a branded collection, so pricing is on enquiry rather than published.',
        spoken:
          'Bugatti Residences by Binghatti is a branded collection, so pricing is on enquiry rather than published.',
        validator: null,
        detail: null,
        figures: null,
      },
    ],
    [40, agentSpeaks(true)],
    [
      20,
      {
        event: 'tts_first_audio',
        turn: 1,
        ms: 1042.1,
        since_first_sentence_ms: 118.6,
      },
    ],
    ...levels(10, 29),
    [
      160,
      {
        event: 'tool_call',
        turn: 1,
        tool: 'escalate_to_human',
        args: { reason: 'branded collection pricing enquiry' },
        at_ms: 1187.4,
        audio_already_played: true,
      },
    ],
    [
      10,
      {
        event: 'escalation',
        reason: 'branded collection pricing enquiry',
        routed_to: 'human_ambassador',
      },
    ],
    [40, usage(1, 44)],
    [
      140,
      brief(1, { intent: 'unknown', stage: 'escalated' }),
    ],
    [
      60,
      {
        event: 'turn_complete',
        turn: 1,
        llm_ttft_ms: 679.3,
        llm_first_sentence_ms: 924.5,
        guardrail_ms: 0.25,
        tts_first_audio_ms: 1042.1,
        total_ms: 1398.7,
        sentences: 1,
        violations: 0,
        regenerated: false,
        actions: ['escalate_to_human'],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    ],
    [200, agentSpeaks(false)],
  ),
}

export const SCRIPTS: readonly ReplayScript[] = [
  ambassadorEnforce,
  naiveWarn,
  naiveEnforce,
  ambassadorWarn,
]

const BY_MODE = new Map(SCRIPTS.map((s) => [modeKey(s.promptMode, s.guardrailMode), s]))

export function scriptFor(
  promptMode: ReplayScript['promptMode'],
  guardrailMode: ReplayScript['guardrailMode'],
): ReplayScript {
  const script = BY_MODE.get(modeKey(promptMode, guardrailMode))
  if (script === undefined) {
    throw new Error(`no replay recorded for ${modeKey(promptMode, guardrailMode)}`)
  }
  return script
}
