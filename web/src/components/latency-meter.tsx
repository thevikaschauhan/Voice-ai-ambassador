'use client'

import { useState } from 'react'
import { Empty, Panel } from '@/components/panel'
import { ms, num, percentile } from '@/lib/format'
import type { TurnView } from '@/lib/session/state'

/**
 * Built for the technical lead, and worth building for him alone.
 *
 * Three things this panel refuses to do, all of which would be easier:
 *
 * 1. It does not stack the stages. `llm_first_sentence` and `tts_first_audio`
 *    are cumulative marks from turn start, not durations, so adding them up
 *    would double-count and inflate the total. The stages are drawn on one
 *    timeline at their real offsets instead.
 * 2. It does not add `stt` to `endpoint`. The framework takes both from the
 *    same anchor, so transcription is a COMPONENT of the endpointing figure
 *    (#21). They are drawn nested; summing them would invent a stage.
 * 3. It does not draw a stage it did not measure. A typed turn has no
 *    end-of-utterance, and the framework reports nothing when its VAD anchors
 *    are missing, so those turns say "not measured" rather than showing a zero.
 *
 * The line this panel exists to support: guardrail validation is a fraction of
 * a millisecond against a turn of roughly one and a half seconds. The safety
 * layer is not what costs you the latency; the model is.
 */

const TARGET_MS = 1200
const CEILING_MS = 1500

interface LatencyMeterProps {
  turns: readonly TurnView[]
}

/**
 * Voice to voice, first audio: what the buyer actually waited.
 *
 * Endpointing happens before the turn tracker's clock starts, so it has to be
 * added back or the headline understates the wait by up to half a second.
 * Without it the figure is the turn's own span and says so.
 */
function voiceToVoice(turn: TurnView): number | null {
  const { endpoint, tts_first_audio } = turn.timings
  if (tts_first_audio === null) return null
  return endpoint === null ? tts_first_audio : endpoint + tts_first_audio
}

export function LatencyMeter({ turns }: LatencyMeterProps) {
  const complete = turns.filter((t) => t.complete)
  const [selected, setSelected] = useState<number | null>(null)
  const turn =
    complete.find((t) => t.turnIndex === selected) ?? complete[complete.length - 1] ?? null

  return (
    <Panel title="Latency" audience="The technical lead">
      {turn === null ? (
        <Empty>
          Per-component timings appear once a turn completes. Every figure here is
          measured on this stack, never taken from a datasheet.
        </Empty>
      ) : (
        <div className="space-y-6">
          {complete.length > 1 ? (
            <TurnPicker
              turns={complete}
              current={turn.turnIndex}
              onSelect={(i) => setSelected(i)}
            />
          ) : null}

          <Timeline turn={turn} />
          <GuardrailCost turn={turn} />
          <TtsConnection turn={turn} />
          <SessionSummary turns={complete} />
          <Usage turn={turn} />
        </div>
      )}
    </Panel>
  )
}

function TurnPicker({
  turns,
  current,
  onSelect,
}: {
  turns: readonly TurnView[]
  current: number
  onSelect: (turnIndex: number) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[12px] text-ink-500">Turn</span>
      {turns.map((t) => (
        <button
          key={t.turnIndex}
          type="button"
          aria-pressed={t.turnIndex === current}
          onClick={() => onSelect(t.turnIndex)}
          className={`tabular border px-2.5 py-1 text-[12px] ${
            t.turnIndex === current
              ? 'border-brass-500 text-brass-400'
              : 'border-ink-700 text-ink-400 hover:border-ink-500'
          }`}
        >
          {t.turnIndex}
        </button>
      ))}
    </div>
  )
}

type Tone = 'input' | 'model' | 'safety' | 'audio'

interface Span {
  label: string
  from: number
  to: number
  detail?: string
  tone: Tone
  nested?: boolean
}

function Timeline({ turn }: { turn: TurnView }) {
  const { endpoint, stt, llm_first_sentence, guardrail, tts_first_audio } = turn.timings
  const ttft = turn.ttftMs
  const total = voiceToVoice(turn) ?? turn.timings.total
  const t0 = endpoint ?? 0

  if (total === null || total <= 0) {
    return <Empty>This turn recorded no timings.</Empty>
  }

  const spans: Span[] = []

  if (endpoint === null) {
    spans.push({
      label: 'Endpointing, speech to text',
      from: 0,
      to: 0,
      detail: 'No end-of-utterance on this turn',
      tone: 'input',
    })
  } else {
    spans.push({
      label: 'Endpointing',
      from: 0,
      to: endpoint,
      detail: 'Buyer stops speaking, to the decision that their turn ended',
      tone: 'input',
    })
    if (stt !== null) {
      spans.push({
        label: 'of which, transcription',
        from: 0,
        to: stt,
        detail: 'Same anchor as endpointing, so a component of it, never added to it',
        tone: 'input',
        nested: true,
      })
      if (turn.afterTranscriptMs !== null) {
        spans.push({
          label: 'of which, detector wait',
          from: stt,
          to: endpoint,
          detail: 'What the detector waited once the words were in hand',
          tone: 'input',
          nested: true,
        })
      }
    }
  }

  if (turn.policyTurn) {
    if (tts_first_audio !== null) {
      spans.push({
        label: 'Confirmation composed and spoken',
        from: t0,
        to: t0 + tts_first_audio,
        detail: 'The policy took the turn, so the model never ran',
        tone: 'safety',
      })
    }
  } else {
    if (ttft !== null) {
      spans.push({
        label: 'LLM time to first token',
        from: t0,
        to: t0 + ttft,
        tone: 'model',
      })
    }
    if (llm_first_sentence !== null) {
      spans.push({
        label: 'LLM to first complete sentence',
        from: t0 + (ttft ?? 0),
        to: t0 + llm_first_sentence,
        detail: 'Sentence boundary, not full response',
        tone: 'model',
      })
    }
    if (guardrail !== null && llm_first_sentence !== null) {
      spans.push({
        label: 'Guardrail and verbalisation',
        from: t0 + llm_first_sentence,
        to: t0 + llm_first_sentence + guardrail,
        detail: `${turn.sentences.length} sentence${turn.sentences.length === 1 ? '' : 's'} inspected`,
        tone: 'safety',
      })
    }
    if (tts_first_audio !== null && llm_first_sentence !== null) {
      spans.push({
        label: 'TTS to first audio',
        from: t0 + llm_first_sentence + (guardrail ?? 0),
        to: t0 + tts_first_audio,
        tone: 'audio',
      })
    }
  }

  const headline = voiceToVoice(turn)

  return (
    <div className="space-y-3">
      {spans.map((s) => (
        <StageRow
          key={s.label}
          label={s.label}
          detail={s.detail}
          value={s.to === s.from && s.tone === 'input' && endpoint === null ? null : s.to - s.from}
          barLeft={(s.from / total) * 100}
          barWidth={((s.to - s.from) / total) * 100}
          tone={s.tone}
          nested={s.nested}
        />
      ))}
      <div className="border-t border-ink-800 pt-3">
        <StageRow
          label="Voice to voice, first audio"
          detail={
            endpoint === null
              ? 'The turn span only. Endpointing was not measured on this turn'
              : 'Endpointing included: it happens before the turn clock starts'
          }
          value={headline}
          barLeft={0}
          barWidth={100}
          tone="audio"
          emphasis
        />
        <p className="tabular mt-2 text-[12px] leading-relaxed text-ink-500">
          Target under {num(TARGET_MS)} ms p50, ceiling {num(CEILING_MS)} ms.{' '}
          {headline === null
            ? null
            : headline <= TARGET_MS
              ? 'Inside target on this turn.'
              : headline <= CEILING_MS
                ? 'Over target, inside ceiling on this turn.'
                : 'Over ceiling on this turn.'}
          {turn.regenerated && headline !== null && headline > TARGET_MS
            ? ' This turn spent a repair retry, which is a second model round trip.'
            : null}
        </p>
      </div>
    </div>
  )
}

function StageRow({
  label,
  detail,
  value,
  barLeft,
  barWidth,
  tone,
  nested = false,
  emphasis = false,
}: {
  label: string
  detail?: string
  value: number | null
  barLeft: number
  barWidth: number
  tone: Tone
  nested?: boolean
  emphasis?: boolean
}) {
  const colour =
    tone === 'safety'
      ? 'bg-brass-400'
      : tone === 'audio'
        ? 'bg-ink-400'
        : tone === 'input'
          ? 'bg-ink-700'
          : 'bg-ink-600'
  return (
    <div className="flex items-baseline gap-4">
      <span
        className={`w-[112px] shrink-0 text-[12px] leading-relaxed sm:w-[190px] ${
          nested ? 'pl-3 text-ink-500' : emphasis ? 'text-ink-200' : 'text-ink-400'
        }`}
      >
        {label}
        {detail ? <span className="block text-[11px] text-ink-600">{detail}</span> : null}
      </span>
      <span className="relative block h-2 flex-1 bg-ink-850">
        {value === null ? (
          <span
            className="absolute top-0 block h-2 border border-dashed border-ink-700"
            aria-hidden
            style={{ left: 0, width: '22%' }}
          />
        ) : (
          <span
            className={`absolute top-0 ${nested ? 'h-[3px] translate-y-[2px]' : 'h-2'} ${colour}`}
            aria-hidden
            style={{
              left: `${clamp(barLeft)}%`,
              // A sub-millisecond stage must still be visible, and must still
              // be visibly tiny. One pixel, no rounding up to a comfortable
              // width.
              width: `max(1px, ${clamp(barWidth)}%)`,
            }}
          />
        )}
      </span>
      <span
        className={`tabular w-[86px] shrink-0 text-right text-[12px] ${
          emphasis ? 'text-ink-100' : 'text-ink-300'
        }`}
      >
        {value === null ? <span className="text-ink-600">not measured</span> : ms(value)}
      </span>
    </div>
  )
}

function GuardrailCost({ turn }: { turn: TurnView }) {
  const guardrail = turn.timings.guardrail
  const total = voiceToVoice(turn) ?? turn.timings.total
  if (guardrail === null || total === null || total <= 0) return null
  const share = (guardrail / total) * 100

  return (
    <p className="border-l border-brass-600 pl-4 text-[13px] leading-relaxed text-ink-300">
      Every sentence on this turn was inspected in{' '}
      <span className="tabular text-ink-100">{ms(guardrail)}</span> in total, which is{' '}
      <span className="tabular text-ink-100">
        {share < 0.1 ? share.toFixed(3) : share.toFixed(2)}%
      </span>{' '}
      of the {ms(total)} the buyer waited. The model dominates the budget, not the safety
      layer.
    </p>
  )
}

/**
 * Issue #18's measurement. `reused: false` on the turn after a barge-in is the
 * defect; `reused: true` on the same turn is the fix, so the panel names which
 * one it is looking at rather than printing a boolean.
 */
function TtsConnection({ turn }: { turn: TurnView }) {
  const connection = turn.ttsConnection
  if (connection === null) return null
  return (
    <p className="text-[12px] leading-relaxed text-ink-500">
      {connection.reused ? (
        <>
          Audio came off a pooled socket, so no handshake sits inside the TTS figure
          above.
        </>
      ) : (
        <>
          The buyer waited{' '}
          <span className="tabular text-ink-300">
            {connection.connectMs === null ? 'a handshake' : ms(connection.connectMs)}
          </span>{' '}
          for a new connection inside the TTS figure above. A pooled socket removes it.
        </>
      )}{' '}
      {num(connection.pooled)} spare {connection.pooled === 1 ? 'socket' : 'sockets'} in
      the pool.
    </p>
  )
}

function SessionSummary({ turns }: { turns: readonly TurnView[] }) {
  const firstAudio = turns
    .filter((t) => !t.policyTurn)
    .map(voiceToVoice)
    .filter((v): v is number => v !== null)
  const p50 = percentile(firstAudio, 50)
  const p90 = percentile(firstAudio, 90)
  if (p50 === null) return null

  return (
    <dl className="grid grid-cols-3 gap-4 border-t border-ink-800 pt-4">
      <Stat label="Turns measured" value={num(firstAudio.length)} />
      <Stat label="p50 first audio" value={ms(p50)} flag={p50 > TARGET_MS} />
      <Stat
        label="p90 first audio"
        value={p90 === null ? '-' : ms(p90)}
        flag={(p90 ?? 0) > CEILING_MS}
      />
    </dl>
  )
}

function Stat({ label, value, flag = false }: { label: string; value: string; flag?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] tracking-wide text-ink-500">{label}</dt>
      <dd className={`tabular mt-1 text-[15px] ${flag ? 'text-flag-400' : 'text-ink-100'}`}>
        {value}
      </dd>
    </div>
  )
}

function Usage({ turn }: { turn: TurnView }) {
  const usage = turn.usage
  if (usage === null) return null
  return (
    <div className="border-t border-ink-800 pt-4">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Stat label="Prompt tokens" value={fmt(usage.promptTokens)} />
        <Stat label="Cached" value={fmt(usage.cachedTokens)} />
        <Stat label="Completion" value={fmt(usage.completionTokens)} />
        <Stat
          label="Reasoning"
          value={fmt(usage.reasoningTokens)}
          flag={!usage.thinkingOff}
        />
      </dl>
      <p className="mt-2.5 text-[12px] leading-relaxed text-ink-500">
        {usage.thinkingOff
          ? 'Thinking is off on this request. Reasoning tokens would run before the first spoken word, and this counter is the only place a silent regression would show.'
          : 'Reasoning tokens were generated on this request. Thinking has been re-enabled somewhere in the path and the latency budget is gone.'}
        {usage.cachedTokens === 0
          ? ' Prompt caching reads zero because the voice path cannot emit the content block that engages it (ADR-016); the counter is plumbed so it shows the day that changes.'
          : null}
      </p>
    </div>
  )
}

function fmt(value: number | null): string {
  return value === null ? 'not measured' : num(value)
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, value))
}
