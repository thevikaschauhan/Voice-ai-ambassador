import type { AgentEvent } from '@/lib/session/events'

/**
 * The seam between the text-mode page and the framework-free core.
 *
 * docs/01- makes text mode the venue plan B: "if venue audio dies, the same
 * core demos as text chat". The word that matters is SAME - text mode is worth
 * having only if it exercises the real pipeline, so this interface returns the
 * same `AgentEvent`s the voice path emits and the page folds them through the
 * same reducer. Nothing in the UI knows which transport produced them.
 *
 * Milestone one ships `replayTextCore`, which is a fixture. Milestone two
 * replaces it with a core-backed implementation and changes nothing else: the
 * core is pure Python with no framework imports (ADR-002), so the server route
 * drives it directly rather than standing up a voice session.
 *
 * The hard rule that shapes this file: the browser never calls a provider.
 * Whatever implements `TextCore` runs server side, which is why the page posts
 * to a route handler instead of doing any of this itself.
 */
export interface TextTurnInput {
  sessionId: string
  turnIndex: number
  text: string
}

export interface TextCore {
  turn(input: TextTurnInput): Promise<AgentEvent[]>
}

interface ScriptedReply {
  /** Every figure below is a record in data/inventory.json or derived from one. */
  match: RegExp
  sentences: { raw: string; spoken: string }[]
  escalate?: string
  shortlist?: string[]
}

const REPLIES: ScriptedReply[] = [
  // Order is precedence: a compound question like "what would I pay upfront
  // on the Bugatti" must reach the branded refusal, not a payment plan for a
  // different project, so the refusals are matched before the answers.
  {
    match: /\b(bugatti|branded)\b/i,
    sentences: [
      {
        raw: 'Bugatti Residences by Binghatti is a branded collection, and pricing there is on enquiry.',
        spoken:
          'Bugatti Residences by Binghatti is a branded collection, and pricing there is on enquiry.',
      },
      {
        raw: 'I have asked one of our ambassadors to come back to you on it directly.',
        spoken: 'I have asked one of our ambassadors to come back to you on it directly.',
      },
    ],
    escalate: 'branded collection pricing enquiry',
  },
  {
    match: /\b(upfront|booking|deposit|down ?payment|payment plan)\b/i,
    sentences: [
      {
        raw: 'On Binghatti Skyrise the booking payment is 20%, which is AED 197,000.',
        spoken:
          'On Binghatti Skyrise the booking payment is twenty percent, which is one hundred and ninety-seven thousand dirhams.',
      },
      {
        raw: 'After that it is 50% across construction and 30% on handover.',
        spoken:
          'After that it is fifty percent across construction and thirty percent on handover.',
      },
    ],
    shortlist: ['binghatti-skyrise'],
  },
  {
    match: /\b(skyrise|business bay)\b/i,
    sentences: [
      {
        raw: 'Binghatti Skyrise in Business Bay starts from AED 985,000.',
        spoken:
          'Binghatti Skyrise in Business Bay starts from nine hundred and eighty-five thousand dirhams.',
      },
      {
        raw: 'Units run from 420 to 1,200 square feet, with handover in Q4 2026.',
        spoken:
          'Units run from four hundred and twenty to one thousand two hundred square feet, with handover in the fourth quarter of 2026.',
      },
    ],
    shortlist: ['binghatti-skyrise'],
  },
  {
    match: /\b(circle|jvc|jumeirah village)\b/i,
    sentences: [
      {
        raw: 'Binghatti Circle in Jumeirah Village Circle starts from AED 650,000.',
        spoken:
          'Binghatti Circle in Jumeirah Village Circle starts from six hundred and fifty thousand dirhams.',
      },
    ],
    shortlist: ['binghatti-circle'],
  },
  {
    match: /\b(aquarise|maritime)\b/i,
    sentences: [
      {
        raw: 'Binghatti Aquarise in Dubai Maritime City starts from AED 1,200,000.',
        spoken:
          'Binghatti Aquarise in Dubai Maritime City starts from one point two million dirhams.',
      },
    ],
    shortlist: ['binghatti-aquarise'],
  },
]

/** Nothing matched: the designed answer is a refusal plus escalation, never a guess. */
const UNKNOWN: ScriptedReply = {
  match: /.*/,
  sentences: [
    {
      raw: 'I do not have that in front of me, and I do not want to tell you something I cannot confirm.',
      spoken:
        'I do not have that in front of me, and I do not want to tell you something I cannot confirm.',
    },
    {
      raw: 'Let me put you through to one of our ambassadors.',
      spoken: 'Let me put you through to one of our ambassadors.',
    },
  ],
  escalate: 'question outside the loaded inventory',
}

export function replayTextCore(): TextCore {
  return {
    async turn({ turnIndex, text }: TextTurnInput): Promise<AgentEvent[]> {
      const reply = REPLIES.find((r) => r.match.test(text)) ?? UNKNOWN
      const events: AgentEvent[] = [
        { event: 'user_turn', turn: turnIndex, text },
        { event: 'llm_ttft', turn: turnIndex, ms: 664.1, model: 'qwen/qwen3.7-flash' },
      ]

      let guardrailTotal = 0
      reply.sentences.forEach((sentence, index) => {
        const cost = 0.24 + index * 0.03
        guardrailTotal += cost
        events.push({
          event: 'guardrail',
          turn: turnIndex,
          outcome: 'pass',
          mode: 'enforce',
          ms: Number(cost.toFixed(2)),
          sentence_index: index,
          raw: sentence.raw,
          spoken: sentence.spoken,
          validator: null,
          detail: null,
          figures: null,
        })
      })

      // The brief extractor is a separate per-turn call that re-extracts from
      // the whole context, so each turn replaces the brief rather than adding
      // to it. Text mode mirrors that rather than accumulating locally.
      events.push({
        event: 'brief',
        turn: turnIndex,
        brief: {
          intent: 'unknown',
          budget: null,
          unit_preference: null,
          timeline: null,
          buyer_location: null,
          golden_visa_interest: null,
          hesitations: [],
          shortlist_ids: reply.shortlist ?? [],
          stage:
            reply.escalate !== undefined
              ? 'escalated'
              : (reply.shortlist ?? []).length > 0
                ? 'recommendation'
                : 'discovery',
          language: 'en',
        },
      })

      if (reply.escalate !== undefined) {
        events.push({
          event: 'tool_call',
          turn: turnIndex,
          tool: 'escalate_to_human',
          args: { reason: reply.escalate },
          at_ms: 912.4,
          audio_already_played: true,
        })
        events.push({
          event: 'escalation',
          reason: reply.escalate,
          routed_to: 'human_ambassador',
        })
      }

      events.push({
        event: 'turn_complete',
        turn: turnIndex,
        llm_ttft_ms: 664.1,
        llm_first_sentence_ms: 908.7,
        guardrail_ms: Number(guardrailTotal.toFixed(2)),
        // Text mode synthesises nothing, so there is no first-audio mark to
        // report. It stays null instead of borrowing the total.
        tts_first_audio_ms: null,
        total_ms: 951.3,
        sentences: reply.sentences.length,
        violations: 0,
        regenerated: false,
        actions: reply.escalate === undefined ? [] : ['escalate_to_human'],
        reasoning_tokens: 0,
        audit_incomplete: false,
      })

      return events
    },
  }
}
