'use client'

import Link from 'next/link'
import { useCallback, useReducer, useRef, useState } from 'react'
import { AmbassadorView } from '@/components/ambassador-view'
import { LatencyMeter } from '@/components/latency-meter'
import { TranscriptRail } from '@/components/transcript-rail'
import type { AgentEvent } from '@/lib/session/events'
import { initialState, reduce } from '@/lib/session/state'
import type { TextModeAvailability } from '@/lib/textmode/availability'
import type { Project } from '@/lib/types'

/**
 * The venue plan B (docs/01-, docs/07-).
 *
 * It shares the reducer and both read-only panels with the voice surface, so
 * what the room sees here is produced by the same fold over the same events.
 * The page never calls a provider: it posts to `/api/text-turn`, which is
 * where the core lives.
 */
export function TextMode({
  projects,
  availability,
}: {
  projects: readonly Project[]
  /**
   * Which of the three states this page is in, decided on the server.
   *
   * A boolean used to be enough, when the only question was whether the core
   * was real. The hosted service adds a third answer - refused - and it is not
   * "replay with a different label": the composer is gone, because there is
   * nothing honest to do with what a visitor would type into it.
   */
  availability: TextModeAvailability
}) {
  const [state, dispatch] = useReducer(reduce, undefined, () =>
    initialState({ connection: 'live' }),
  )
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const turnRef = useRef(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const live = availability === 'real'
  const refused = availability === 'refused'

  const send = useCallback(
    async (text: string) => {
      turnRef.current += 1
      setPending(true)
      setFailure(null)
      try {
        const response = await fetch('/api/text-turn', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            sessionId: 'text-mode',
            turnIndex: turnRef.current,
            text,
          }),
        })
        if (!response.ok) throw new Error(`route returned ${response.status}`)
        const { events } = (await response.json()) as { events: AgentEvent[] }
        for (const event of events) dispatch(event)
      } catch {
        // A turn never ends in silence, on any transport. The composed copy is
        // the reply, and it is the line that hands the buyer to a human.
        dispatch({ event: 'user_turn', turn: turnRef.current, text })
        dispatch({
          event: 'fallback',
          turn: turnRef.current,
          text: 'I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.',
          reason: 'transport',
        })
        setFailure('The turn could not reach the core, so the composed handover stood in.')
      } finally {
        setPending(false)
      }
    },
    [],
  )

  return (
    <main className="mx-auto flex min-h-screen max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">
              Text mode
            </h1>
            <span
              className={`border px-2.5 py-1 text-[11px] tracking-[0.12em] uppercase ${
                live ? 'border-brass-500 text-brass-400' : 'border-ink-700 text-ink-400'
              }`}
            >
              {live ? 'Live core' : refused ? 'Unavailable' : 'Replay'}
            </span>
          </div>
          <p className="mt-1.5 max-w-[74ch] text-[12px] leading-relaxed text-ink-500">
            {live
              ? 'The same core, demonstrated as chat: the same prompt, guardrail, recovery policy and escalation routing a call runs. This is the plan for a venue where the audio fails. The stages that only exist on the voice path report themselves missing rather than reporting a zero.'
              : refused
                ? 'Text mode runs the ambassador as chat, and it needs the agent on the same machine as this page. On this hosted demo they are separate services, so rather than answer you from a script and call it the ambassador, this page does not answer at all. Use the call instead - that is the real thing.'
                : 'Scripted replies, not the core. No agent is configured, so this shows the shape of text mode without running the pipeline behind it.'}
          </p>
        </div>
        <Link className="text-[12px] text-ink-400 hover:text-brass-400" href="/">
          Call surface
        </Link>
      </header>

      {failure ? (
        <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300" role="status">
          {failure}
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr] lg:items-start">
        <div className="flex flex-col gap-6">
          <TranscriptRail turns={state.turns} />
          {refused ? (
            <p
              className="border border-ink-700 px-5 py-3.5 text-[13px] leading-relaxed text-ink-400"
              role="status"
            >
              Not available here. Start a call instead.{' '}
              <Link className="text-ink-300 underline hover:text-brass-400" href="/talk">
                Talk to the ambassador
              </Link>
              .
            </p>
          ) : (
          <form
            className="flex gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              const value = inputRef.current?.value.trim() ?? ''
              if (value === '' || pending) return
              if (inputRef.current) inputRef.current.value = ''
              void send(value)
            }}
          >
            <label className="sr-only" htmlFor="text-mode-input">
              Message the ambassador
            </label>
            <input
              id="text-mode-input"
              ref={inputRef}
              type="text"
              autoComplete="off"
              placeholder="Ask about a project, a price, or a payment plan"
              className="flex-1 border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100 placeholder:text-ink-600"
            />
            <button
              type="submit"
              disabled={pending}
              className="border border-ink-600 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
            >
              {pending ? 'Sending' : 'Send'}
            </button>
          </form>
          )}
        </div>
        <div className="flex flex-col gap-6">
          <AmbassadorView state={state} projects={projects} />
          {/* The tech lead is still in the room when the audio has failed, and
              this is where the meter's "not measured" rendering earns itself:
              a typed turn has no end-of-utterance and no synthesis. */}
          <LatencyMeter turns={state.turns} />
        </div>
      </div>
    </main>
  )
}
