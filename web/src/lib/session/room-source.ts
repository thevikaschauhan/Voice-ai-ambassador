'use client'

import { joinRoom } from '@/lib/session/room-signals'
import type { RoomGrant, RoomHandle } from '@/lib/session/room-signals'
import type { SessionSource } from '@/lib/session/source'

/**
 * The room, as a session source.
 *
 * It asks this server for a listen-only ticket, joins, and emits amplitude and
 * speaking state. Everything about the call's CONTENT still comes from the
 * events bridge; this only adds what audio can tell you and the event stream
 * cannot.
 *
 * Failure is not an error state. No LiveKit configured, no room open, an
 * expired token: each one leaves the surface exactly where it was before this
 * existed, showing "no audio track attached", which is true. A room client that
 * turned a missing room into a broken page would make the demo more fragile
 * than the placeholder it replaced.
 */
export function roomSource(url = '/api/session/room'): SessionSource {
  return {
    start(emit, onEnd) {
      let handle: RoomHandle | null = null
      let stopped = false

      void (async () => {
        try {
          const response = await fetch(url, { cache: 'no-store' })
          if (!response.ok) {
            emit({ signal: 'audio_source', kind: 'none' })
            onEnd?.()
            return
          }
          const grant = (await response.json()) as RoomGrant
          if (stopped) return
          handle = await joinRoom(grant, emit)
          if (stopped) {
            await handle.disconnect()
            handle = null
            return
          }
          emit({ signal: 'audio_source', kind: 'room' })
        } catch {
          // Same posture as above: the surface keeps the honest placeholder.
          emit({ signal: 'audio_source', kind: 'none' })
          onEnd?.()
        }
      })()

      return () => {
        stopped = true
        emit({ signal: 'audio_source', kind: 'none' })
        void handle?.disconnect()
        handle = null
      }
    },
  }
}
