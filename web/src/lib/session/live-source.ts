'use client'

import type { AnyAgentEvent, SessionInput } from '@/lib/session/events'
import type { Emit, SessionSource } from '@/lib/session/source'

/**
 * The live half of the seam `replaySource` has been standing in for.
 *
 * It consumes the same-origin SSE stream from `/api/session/stream` and emits
 * the agent's events into the same `reduce()` every panel already reads. That
 * is the whole payoff of milestone one having a source interface: nothing below
 * this file changes when a real call replaces a fixture.
 *
 * It never talks to the agent directly. The token lives on the Next server, so
 * this speaks same-origin to Next and Next speaks loopback to the agent.
 */
export function liveSource(url = '/api/session/stream'): SessionSource {
  return {
    start(emit: Emit, onEnd?: () => void) {
      const source = new EventSource(url)
      let stopped = false

      const stop = () => {
        if (stopped) return
        stopped = true
        source.close()
      }

      source.addEventListener('open', () => {
        emit({ signal: 'connection', state: 'live' })
      })

      source.addEventListener('agent', (event) => {
        if (stopped) return
        const parsed = parse((event as MessageEvent<string>).data)
        if (parsed === null) return
        emit(parsed)
        for (const derived of transportSignals(parsed)) emit(derived)
      })

      source.addEventListener('close', () => {
        emit({ signal: 'connection', state: 'ended' })
        stop()
        onEnd?.()
      })

      source.addEventListener('error', () => {
        // EventSource reconnects on its own, so an error is "the link dropped",
        // not "the call ended" - and those render differently. The stream is
        // left open so a demo laptop's wifi blinking does not end the session.
        emit({ signal: 'connection', state: 'lost' })
      })

      // Nothing is ever sent up this connection: the bridge is read-only and
      // there is no command channel to reach through it.
      return stop
    },
  }
}

function parse(data: string): AnyAgentEvent | null {
  try {
    const value: unknown = JSON.parse(data)
    if (typeof value !== 'object' || value === null) return null
    const event = (value as { event?: unknown }).event
    return typeof event === 'string' ? (value as AnyAgentEvent) : null
  } catch {
    // A malformed line is a framing bug, not a turn. Dropping one is better
    // than tearing down a live call over it.
    return null
  }
}

/**
 * Who is speaking, inferred from the agent's own events.
 *
 * The honest bounds of this: these are turn-level facts, not audio. The agent
 * emits when a turn arrives and when its first audio plays, so "the ambassador
 * started speaking" is exact and "the buyer is speaking" is only known after
 * they stopped. Waveform levels are not derivable at all - they need the audio
 * track, which is the LiveKit room and a separate piece of work. The call panel
 * says which of these it is showing rather than implying a microphone.
 */
function transportSignals(event: AnyAgentEvent): SessionInput[] {
  switch (event.event) {
    case 'user_turn':
      // The turn is already transcribed by the time it is emitted, so this is
      // the END of the buyer speaking, not the start.
      return [{ signal: 'buyer_speaking', on: false }]
    case 'tts_first_audio':
    case 'budget_confirmation_spoken':
      return [{ signal: 'agent_speaking', on: true }]
    case 'interrupted':
      // Barge-in: the buyer talked over playback. The reducer reads a buyer
      // signal arriving while the agent speaks as the barge-in indicator, so
      // the order of these two matters.
      return [
        { signal: 'buyer_speaking', on: true },
        { signal: 'agent_speaking', on: false },
      ]
    case 'turn_complete':
      return [{ signal: 'agent_speaking', on: false }]
    default:
      return []
  }
}
