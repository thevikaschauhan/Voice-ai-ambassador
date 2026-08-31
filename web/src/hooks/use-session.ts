'use client'

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { SessionInput } from '@/lib/session/events'
import type { SessionSource } from '@/lib/session/source'
import { initialState, reduce } from '@/lib/session/state'
import type { SessionState } from '@/lib/session/state'

export type PlaybackStatus = 'idle' | 'running' | 'finished'

export interface UseSession {
  state: SessionState
  status: PlaybackStatus
  start: () => void
  stop: () => void
}

/**
 * Drives the reducer from whichever source it was handed.
 *
 * This is where milestone one's seam pays off: `replaySource` and `liveSource`
 * are interchangeable here, and no panel below knows which one is running.
 */
export function useSession(
  source: SessionSource,
  initial: Partial<SessionState> = {},
): UseSession {
  const [state, dispatch] = useReducer(reduce, undefined, () => initialState(initial))
  const [status, setStatus] = useState<PlaybackStatus>('idle')
  const stopRef = useRef<(() => void) | null>(null)

  const stop = useCallback(() => {
    stopRef.current?.()
    stopRef.current = null
  }, [])

  const start = useCallback(() => {
    stop()
    dispatch({ signal: 'connection', state: 'connecting' })
    setStatus('running')
    stopRef.current = source.start(
      (input: SessionInput) => dispatch(input),
      () => setStatus('finished'),
    )
  }, [source, stop])

  // A source change is a session change: the modes are process configuration,
  // so flipping a toggle restarts the call rather than mutating it mid-flight.
  // The component keys on that change, so this only has to cover unmount.
  useEffect(() => stop, [stop])

  return { state, status, start, stop }
}
