'use client'

import { useState } from 'react'
import { Empty, Panel } from '@/components/panel'
import { ms, num, percentile } from '@/lib/format'
import type { TurnView } from '@/lib/session/state'

/**
 * Built for the technical lead, and worth building for him alone.
 *
 * Two things this panel refuses to do, both of which would be easier:
 *
 * 1. It does not stack the stages. `llm_first_sentence` and `tts_first_audio`
 *    are cumulative marks from turn start, not durations, so adding them up
 *    would double-count and inflate the total. The stages are drawn on a
 *    timeline at their real offsets instead.
 * 2. It does not draw a stage it did not measure. `Timings.endpoint` and
 *    `Timings.stt` exist on the model but `TurnTracker.finish()` never
 *    populates them, so they render as an explicit unmeasured region rather
 *    than as zero - events.py's own rule, applied to the panel it was written
 *    for.
 *
 * The line this panel exists to support: guardrail validation is a fraction of
 * a millisecond against a turn of roughly one second. The safety layer is not
 * what costs you the latency; the model is.
 */

const TARGET_MS = 1200
const CEILING_MS = 1500

interface LatencyMeterProps {
  turns: readonly TurnView[]
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

interface Span {
  label: string
  from: number
  to: number
  detail?: string
  tone: 'model' | 'safety' | 'audio'
}

function Timeline({ turn }: { turn: TurnView }) {
  const { llm_first_sentence, guardrail, tts_first_audio } = turn.timings
  const ttft = turn.ttftMs
  const span = tts_first_audio ?? llm_first_sentence ?? turn.timings.total

  if (turn.policyTurn) {
    return (
      <div className="space-y-3">
        <StageRow
          label="Confirmation composed and spoken"
          value={tts_first_audio}
          barLeft={0}
          barWidth={100}
          tone="safety"
        />
        <p className="text-[12px] leading-relaxed text-ink-500">
          The deterministic budget policy took this turn, so there is no model timing to
          report. This is the whole cost of a turn the model never ran: roughly a fifth of
          a generated one.
        </p>
      </div>
    )
  }

  if (span === null || span <= 0) {
    return <Empty>This turn recorded no timings.</Empty>
  }

  const spans: Span[] = []
  if (ttft !== null) {
    spans.push({ label: 'LLM time to first token', from: 0, to: ttft, tone: 'model' })
  }
  if (llm_first_sentence !== null) {
    spans.push({
      label: 'LLM to first complete sentence',
      from: ttft ?? 0,
      to: llm_first_sentence,
      detail: 'Sentence boundary, not full response',
      tone: 'model',
    })
  }
  if (guardrail !== null && llm_first_sentence !== null) {
    spans.push({
      label: 'Guardrail and verbalisation',
      from: llm_first_sentence,
      to: llm_first_sentence + guardrail,
      detail: `${turn.sentences.length} sentence${turn.sentences.length === 1 ? '' : 's'} inspected`,
      tone: 'safety',
    })
  }
  if (tts_first_audio !== null && llm_first_sentence !== null) {
    spans.push({
      label: 'TTS to first audio',
      from: llm_first_sentence + (guardrail ?? 0),
      to: tts_first_audio,
      tone: 'audio',
    })
  }

  return (
    <div className="space-y-3">
      <UnmeasuredRow />
      {spans.map((s) => (
        <StageRow
          key={s.label}
          label={s.label}
          detail={s.detail}
          value={s.to - s.from}
          barLeft={(s.from / span) * 100}
          barWidth={((s.to - s.from) / span) * 100}
          tone={s.tone}
        />
      ))}
      <div className="border-t border-ink-800 pt-3">
        <StageRow
          label="Voice to voice, first audio"
          value={tts_first_audio}
          barLeft={0}
          barWidth={100}
          tone="audio"
          emphasis
        />
        <p className="tabular mt-2 text-[12px] text-ink-500">
          Target under {num(TARGET_MS)} ms p50, ceiling {num(CEILING_MS)} ms.{' '}
          {tts_first_audio === null
            ? null
            : tts_first_audio <= TARGET_MS
              ? 'Inside target on this turn.'
              : tts_first_audio <= CEILING_MS
                ? 'Over target, inside ceiling on this turn.'
                : 'Over ceiling on this turn.'}
        </p>
      </div>
    </div>
  )
}

/**
 * Endpointing and speech to text happen before the turn tracker's clock
 * starts, and nothing populates their fields. Saying so is the point.
 */
function UnmeasuredRow() {
  return (
    <div className="flex items-baseline gap-4">
      <span className="w-[112px] shrink-0 text-[12px] leading-relaxed text-ink-500 sm:w-[190px]">
        Endpointing, speech to text
      </span>
      <span className="flex-1">
        <span
          className="block h-2 border border-dashed border-ink-700"
          aria-hidden
          style={{ width: '22%' }}
        />
      </span>
      <span className="w-[86px] shrink-0 text-right text-[12px] text-ink-600">
        not measured
      </span>
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
  emphasis = false,
}: {
  label: string
  detail?: string
  value: number | null
  barLeft: number
  barWidth: number
  tone: Span['tone']
  emphasis?: boolean
}) {
  const colour =
    tone === 'safety' ? 'bg-brass-400' : tone === 'audio' ? 'bg-ink-400' : 'bg-ink-600'
  return (
    <div className="flex items-baseline gap-4">
      <span
        className={`w-[112px] shrink-0 text-[12px] leading-relaxed sm:w-[190px] ${
          emphasis ? 'text-ink-200' : 'text-ink-400'
        }`}
      >
        {label}
        {detail ? <span className="block text-[11px] text-ink-600">{detail}</span> : null}
      </span>
      <span className="relative block h-2 flex-1 bg-ink-850">
        <span
          className={`absolute top-0 h-2 ${colour}`}
          aria-hidden
          style={{
            left: `${clamp(barLeft)}%`,
            // A sub-millisecond stage must still be visible, and must still be
            // visibly tiny. One pixel, no rounding up to a comfortable width.
            width: `max(1px, ${clamp(barWidth)}%)`,
          }}
        />
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
  const total = turn.timings.tts_first_audio ?? turn.timings.total
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

function SessionSummary({ turns }: { turns: readonly TurnView[] }) {
  const firstAudio = turns
    .filter((t) => !t.policyTurn)
    .map((t) => t.timings.tts_first_audio)
    .filter((v): v is number => v !== null)
  const p50 = percentile(firstAudio, 50)
  const p90 = percentile(firstAudio, 90)
  if (p50 === null) return null

  return (
    <dl className="grid grid-cols-3 gap-4 border-t border-ink-800 pt-4">
      <Stat label="Turns measured" value={String(firstAudio.length)} />
      <Stat label="p50 first audio" value={ms(p50)} flag={p50 > TARGET_MS} />
      <Stat label="p90 first audio" value={p90 === null ? '-' : ms(p90)} flag={(p90 ?? 0) > CEILING_MS} />
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
