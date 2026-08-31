'use client'

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { SessionInput } from '@/lib/session/events'
import { replaySource } from '@/lib/session/source'
import type { ReplayScript } from '@/lib/session/scripts/types'
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
 * Drives the reducer from a replay script. The only thing that changes in
 * milestone two is which source is constructed here.
 */
export function useSession(script: ReplayScript, speed = 1): UseSession {
  const [state, dispatch] = useReducer(reduce, undefined, () =>
    initialState({
      promptMode: script.promptMode,
      guardrailMode: script.guardrailMode,
    }),
  )
  const [status, setStatus] = useState<PlaybackStatus>('idle')
  const stopRef = useRef<(() => void) | null>(null)
  const source = useMemo(() => replaySource(script, speed), [script, speed])

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

  // A script change is a session change: the modes are process configuration,
  // so flipping a toggle restarts the call rather than mutating it mid-flight.
  useEffect(() => stop, [stop])

  return { state, status, start, stop }
}
