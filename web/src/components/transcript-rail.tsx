'use client'

import { useEffect, useRef } from 'react'
import { Empty, Panel } from '@/components/panel'
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'
import type { SentenceDecision, TurnView } from '@/lib/session/state'

interface TranscriptRailProps {
  turns: readonly TurnView[]
}

/**
 * What the room follows.
 *
 * Spoken text leads, because that is what the buyer heard. The model's own
 * sentence appears only where the two differ - a blocked sentence, a warned
 * one, or a composed recovery - which is exactly where the difference is the
 * story (docs/03-).
 */
export function TranscriptRail({ turns }: TranscriptRailProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const reduced = usePrefersReducedMotion()

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'end' })
  }, [turns, reduced])

  return (
    <Panel title="Transcript" audience="The room" className="min-h-[280px]">
      {turns.length === 0 ? (
        <Empty>The call has not started. Turns appear here as they are spoken.</Empty>
      ) : (
        <ol className="space-y-7">
          {turns.map((turn) => (
            <li key={turn.turnIndex} className="space-y-3">
              {turn.buyerUtterance ? (
                <Line speaker="Buyer" text={turn.buyerUtterance} muted />
              ) : null}

              {turn.spokenChunks.map((chunk, i) => (
                <div key={i}>
                  <Line speaker="Ambassador" text={chunk.text} />
                  {chunk.completed ? null : (
                    <p className="mt-1 text-[11px] tracking-wide text-flag-400 sm:pl-[108px]">
                      Playback cut by barge-in. Audited at chunk granularity, not word
                      level.
                    </p>
                  )}
                </div>
              ))}

              <Decisions turn={turn} />
            </li>
          ))}
        </ol>
      )}
      <div ref={endRef} />
    </Panel>
  )
}

function Line({
  speaker,
  text,
  muted = false,
}: {
  speaker: string
  text: string
  muted?: boolean
}) {
  // The label sits above the line on a narrow screen and beside it from `sm`
  // up. Fixed at 92px because "AMBASSADOR" at this size and tracking is 84px
  // wide, and a label narrower than its own longest word overlaps the speech.
  return (
    <p className="text-[14px] leading-relaxed sm:flex sm:gap-4">
      <span className="block pt-0.5 text-[11px] tracking-[0.1em] text-ink-500 uppercase sm:w-[92px] sm:shrink-0">
        {speaker}
      </span>
      <span className={muted ? 'text-ink-400' : 'text-ink-100'}>{text}</span>
    </p>
  )
}

function Decisions({ turn }: { turn: TurnView }) {
  const flagged = turn.sentences.filter((s) => s.outcome !== 'pass')
  const composed = turn.composed
  if (flagged.length === 0 && composed.length === 0 && !turn.policyTurn) return null

  return (
    <div className="space-y-2.5 border-l border-ink-800 pl-4 sm:ml-[108px]">
      {turn.policyTurn ? (
        <Note>
          The deterministic budget policy took this turn, so the model never ran. The
          question cannot be skipped, reworded, or answered on the buyer&rsquo;s behalf.
        </Note>
      ) : null}

      {flagged.map((sentence) => (
        <Flagged key={sentence.index} sentence={sentence} />
      ))}

      {composed.map((item, i) => (
        <Note key={i}>
          {item.kind === 'bridge'
            ? 'Audio had already played, so the violating sentence was replaced by fixed composed copy rather than retried.'
            : item.kind === 'fallback'
              ? 'Nothing had been spoken, so fixed composed copy became the whole reply. It is the line that hands the buyer to a human.'
              : 'Composed by the confirmation policy from the buyer’s own words. No model output passes through it.'}
        </Note>
      ))}

      {turn.regenerated ? (
        <Note>One repair retry was spent regenerating the blocked sentence.</Note>
      ) : null}
    </div>
  )
}

function Flagged({ sentence }: { sentence: SentenceDecision }) {
  const blocked = sentence.outcome === 'blocked'
  return (
    <div className="space-y-1.5">
      <p className="flex flex-wrap items-baseline gap-x-3 text-[11px] tracking-wide">
        <span className={blocked ? 'text-flag-400' : 'text-warn-500'}>
          {blocked ? 'Blocked before synthesis' : 'Recorded, spoken anyway'}
        </span>
        <span className="text-ink-500">
          {sentence.violation?.validator ?? 'guardrail'} &middot; {sentence.ms.toFixed(2)} ms
          &middot; mode {sentence.mode}
        </span>
      </p>
      <p
        className={`text-[13px] leading-relaxed ${
          blocked ? 'text-ink-500 line-through decoration-flag-500/60' : 'text-ink-300'
        }`}
      >
        {sentence.raw}
      </p>
      {sentence.violation ? (
        <p className="text-[12px] leading-relaxed text-ink-500">
          {sentence.violation.detail}
          {sentence.violation.figures.length > 0
            ? ` (${sentence.violation.figures.map((f) => f.surface).join(', ')})`
            : ''}
        </p>
      ) : null}
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-[12px] leading-relaxed text-ink-500">{children}</p>
}
