'use client'

import { rms } from '@/lib/audio/level'

/**
 * How loud each side of the call is, sampled continuously.
 *
 * This exists so the orb can bloom with a voice rather than with a state
 * change. `Participant.audioLevel` and `ActiveSpeakersChanged` were the other
 * option and are not used: `audioLevel` updates on the order of once a second,
 * which is a meter reading rather than something speech can drive, and
 * `room-signals.ts` already settled the rule for this repository - one number,
 * both readings, because a corona blooming while the label says silent is
 * worse than either reading alone.
 *
 * The analyser is NOT connected to `audioContext.destination`. The agent's
 * track is already attached to an audio element and playing; connecting the
 * graph as well would play it twice.
 */

/** 20 samples a second: smooth enough to look like speech, cheap enough to run. */
const SAMPLE_MS = 50

/** Below this a side is silent, not quiet. */
export const SPEAKING_FLOOR = 0.05

/**
 * How fast the reading may rise and fall.
 *
 * A corona driven by the raw sample strobes at 20Hz on ordinary speech, which
 * looks like a fault rather than a voice. Rising fast keeps the bloom on the
 * attack of a word; falling slowly keeps it from flickering between syllables.
 */
const ATTACK = 0.55
const RELEASE = 0.12

export interface Levels {
  agent: number
  visitor: number
}

export interface LevelMeter {
  /** Attach a track to one side. Idempotent per track id. */
  add: (id: string, track: MediaStreamTrack, side: keyof Levels) => void
  remove: (id: string) => void
  stop: () => void
}

export function levelMeter(onLevels: (levels: Levels) => void): LevelMeter {
  const analysers = new Map<string, { analyser: AnalyserNode; side: keyof Levels }>()
  const smoothed: Levels = { agent: 0, visitor: 0 }
  let context: AudioContext | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  const ensureContext = (): AudioContext => {
    context ??= new AudioContext()
    // A context created outside a gesture starts suspended, and a suspended
    // analyser returns silence rather than an error - the orb would sit still
    // through an audible call, which is the one thing it must not do. Starting
    // a call is a gesture, so this normally does nothing.
    if (context.state === 'suspended') void context.resume()
    return context
  }

  const ensureTimer = () => {
    if (timer !== null) return
    timer = setInterval(() => {
      const raw: Levels = { agent: 0, visitor: 0 }
      for (const { analyser, side } of analysers.values()) {
        raw[side] = Math.max(raw[side], Math.min(1, rms(analyser) * 4))
      }
      for (const side of ['agent', 'visitor'] as const) {
        const rate = raw[side] > smoothed[side] ? ATTACK : RELEASE
        const next = smoothed[side] + (raw[side] - smoothed[side]) * rate
        // Snapped to zero so a decaying tail does not hold the orb open.
        smoothed[side] = next < SPEAKING_FLOOR / 4 ? 0 : next
      }
      onLevels({ ...smoothed })
    }, SAMPLE_MS)
  }

  return {
    add(id, track, side) {
      if (analysers.has(id)) return
      const audio = ensureContext()
      const analyser = audio.createAnalyser()
      analyser.fftSize = 512
      audio.createMediaStreamSource(new MediaStream([track])).connect(analyser)
      analysers.set(id, { analyser, side })
      ensureTimer()
    },
    remove(id) {
      analysers.delete(id)
    },
    stop() {
      if (timer !== null) clearInterval(timer)
      timer = null
      analysers.clear()
      void context?.close().catch(() => {})
      context = null
      onLevels({ agent: 0, visitor: 0 })
    },
  }
}
