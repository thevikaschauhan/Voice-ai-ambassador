'use client'

import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

interface WaveformProps {
  levels: readonly number[]
  active: boolean
  /** Whose audio the levels belong to, for the accessible label. */
  label: string
}

const BARS = 40

/**
 * Input levels, drawn as bars.
 *
 * Under `prefers-reduced-motion` the moving trace is replaced by a static
 * level reading rather than a faster version of the same movement - the
 * accommodation is the absence of motion, not less of it.
 */
export function Waveform({ levels, active, label }: WaveformProps) {
  const reduced = usePrefersReducedMotion()
  const recent = levels.slice(-BARS)
  const peak = recent.length === 0 ? 0 : Math.max(...recent)

  if (reduced) {
    return (
      <div
        className="flex h-14 items-center gap-3 border border-ink-800 px-4"
        role="img"
        aria-label={`${label}: peak level ${Math.round(peak * 100)} per cent`}
      >
        <div className="h-1.5 flex-1 bg-ink-800">
          <div
            className="h-full bg-ink-500"
            style={{ width: `${Math.round(peak * 100)}%` }}
          />
        </div>
        <span className="tabular text-[11px] text-ink-500">
          {Math.round(peak * 100)}%
        </span>
      </div>
    )
  }

  return (
    <div
      className="flex h-14 items-center gap-[3px] border border-ink-800 px-4"
      role="img"
      aria-label={`${label}: ${active ? 'audio present' : 'silent'}`}
    >
      {Array.from({ length: BARS }, (_, i) => {
        const level = recent[recent.length - BARS + i] ?? 0
        const height = Math.max(2, Math.round(level * 34))
        return (
          <span
            key={i}
            className={`w-[3px] shrink-0 ${active ? 'bg-brass-500' : 'bg-ink-700'}`}
            style={{ height: `${height}px`, transition: 'height 90ms linear' }}
          />
        )
      })}
    </div>
  )
}
