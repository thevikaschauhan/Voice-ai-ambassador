/**
 * Where session inputs come from.
 *
 * Milestone one ships `replaySource`. Milestone two adds a live source backed
 * by a LiveKit room (transport signals) and an events bridge from the agent
 * process (agent events). Both satisfy this interface and both feed
 * `reduce()`, so no panel changes when the second one arrives.
 */

import type { SessionInput } from '@/lib/session/events'
import type { ReplayScript } from '@/lib/session/scripts/types'

export type Emit = (input: SessionInput) => void

export interface SessionSource {
  /** Begin emitting. Returns a stop function; calling it twice is safe. */
  start(emit: Emit, onEnd?: () => void): () => void
}

export function replaySource(script: ReplayScript, speed = 1): SessionSource {
  return {
    start(emit, onEnd) {
      const timers: ReturnType<typeof setTimeout>[] = []
      let elapsed = 0
      let stopped = false

      for (const step of script.steps) {
        elapsed += step.after
        timers.push(
          setTimeout(() => {
            if (!stopped) emit(step.input)
          }, elapsed / speed),
        )
      }
      timers.push(
        setTimeout(() => {
          if (!stopped) onEnd?.()
        }, elapsed / speed + 1),
      )

      return () => {
        stopped = true
        for (const timer of timers) clearTimeout(timer)
        timers.length = 0
      }
    },
  }
}
