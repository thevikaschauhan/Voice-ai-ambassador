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

/**
 * Which source is running. It is on screen at all times and it is the reason
 * this type is not a private detail of a component: the one unrecoverable
 * mistake this surface could make is letting somebody believe a fixture was a
 * call.
 */
export type Provenance = 'live' | 'replay'

export interface SessionSource {
  /** Begin emitting. Returns a stop function; calling it twice is safe. */
  start(emit: Emit, onEnd?: () => void): () => void
}

/**
 * Run several sources into one session.
 *
 * The live surface is two feeds, not one: the events bridge carries turns,
 * guardrail decisions and timings, and the LiveKit room carries amplitude and
 * who is talking. They are separate because they are separately available - a
 * machine with no room still gets a full transcript - and folding them here
 * means the panels keep seeing a single stream.
 *
 * `onEnd` fires when every source has ended, not the first: the room going
 * quiet does not mean the call is over.
 */
export function combine(...sources: SessionSource[]): SessionSource {
  return {
    start(emit, onEnd) {
      let ended = 0
      const stops = sources.map((source) =>
        source.start(emit, () => {
          ended += 1
          if (ended === sources.length) onEnd?.()
        }),
      )
      return () => {
        for (const stop of stops) stop()
      }
    },
  }
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
