'use client'

import Link from 'next/link'
import { useCallback, useMemo, useRef, useState } from 'react'
import { TalkOrb } from '@/components/talk-orb'
import type { OrbState } from '@/components/talk-orb'
import { TalkSubtitles } from '@/components/talk-subtitles'
import { AMBASSADOR_FALLBACK } from '@/lib/ambassador.shared'
import type { AmbassadorNames } from '@/lib/ambassador.shared'
import type { TalkLanguage } from '@/lib/livekit/talk.shared'
import { SPEAKING_FLOOR } from '@/lib/talk/levels'
import type { Levels } from '@/lib/talk/levels'
import { startTalking } from '@/lib/talk/session'
import type { TalkEnding, TalkHandle, TalkLine, TalkPhase } from '@/lib/talk/session'

/**
 * The client-facing talk surface.
 *
 * The demanding case this page was built for: the URL is shared with the client
 * so they can try the POC at their end, with nobody from us present (docs/09-).
 * An unattended visitor cannot be handed a caveat out loud, so everything this
 * page cannot honestly do, it says on itself.
 *
 * It shows a transcript and nothing else. The latency meter, the guardrail and
 * violation panels and the ambassador brief carry unredacted records and stay
 * loopback-bound (issue #30) - they are the tech lead's screen in a meeting,
 * not the client's. The sentence at the bottom names them rather than replaying
 * a fixture into them.
 */

/** The phases in which a call is up, so the orb and the controls agree. */
const inCallPhases: ReadonlySet<string> = new Set(['connecting', 'live', 'reconnecting'])

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'ar', label: 'العربية' },
  { code: 'hi', label: 'हिन्दी' },
] as const

type LanguageCode = (typeof LANGUAGES)[number]['code']

export function TalkCall({
  names,
  offered = ['en', 'ar', 'hi'],
}: {
  names: AmbassadorNames
  /**
   * The languages this deployment offers, decided on the server. Defaulted so
   * a test or a future caller that does not care gets all three, which is also
   * the unset behaviour.
   */
  offered?: readonly TalkLanguage[]
}) {
  const [code, setCode] = useState('')
  // The first offered language, not a hardcoded 'en': a deployment offering
  // Hindi only should not open with an English selection its own picker cannot
  // show.
  const [language, setLanguage] = useState<LanguageCode>(offered[0] ?? 'en')
  const [phase, setPhase] = useState<TalkPhase | 'idle' | 'starting'>('idle')
  const [refusal, setRefusal] = useState<string | null>(null)
  const [trouble, setTrouble] = useState<string | null>(null)
  const [muted, setMuted] = useState(false)
  const [lines, setLines] = useState<readonly TalkLine[]>([])
  const [ending, setEnding] = useState<TalkEnding | null>(null)
  const [levels, setLevels] = useState<Levels>({ agent: 0, visitor: 0 })
  const handleRef = useRef<TalkHandle | null>(null)

  /**
   * Whose turn it is, from the levels and the rail rather than from a flag the
   * agent would have to send. Precedence matters: the ambassador speaking wins
   * over the visitor, because the corona should follow whoever is audible and
   * an open microphone picks her up through the speakers.
   */
  const name = names[language]?.trim() || AMBASSADOR_FALLBACK
  // The offered set, in the product's order, with the labels the picker shows.
  const choices = LANGUAGES.filter((option) => offered.includes(option.code))
  const orb: OrbState = useMemo(() => {
    if (!inCallPhases.has(phase)) return 'idle'
    if (levels.agent >= SPEAKING_FLOOR) return 'speaking'
    if (levels.visitor >= SPEAKING_FLOOR) return 'visitor'
    // A visitor line that has been finalised with nothing from the ambassador
    // yet IS the thinking pause - the one moment a silent page reads as broken.
    const last = lines.length > 0 ? lines[lines.length - 1] : null
    if (last !== null && last.speaker === 'visitor' && last.final) return 'thinking'
    return 'listening'
  }, [phase, levels.agent, levels.visitor, lines])

  const record = useCallback((line: TalkLine) => {
    // Keyed on the stream id, so a segment that arrives in ten chunks is one
    // line that grows rather than ten lines that repeat.
    setLines((current) => {
      const index = current.findIndex((existing) => existing.id === line.id)
      if (index === -1) return [...current, line]
      const next = [...current]
      next[index] = line
      return next
    })
  }, [])

  const start = useCallback(async () => {
    setRefusal(null)
    setTrouble(null)
    setEnding(null)
    // The previous call's transcript goes here rather than after the connect,
    // so a second call never opens with the last one's words still on screen.
    setLines([])
    setPhase('starting')
    try {
      const response = await fetch('/api/talk', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ code, language }),
        cache: 'no-store',
      })
      const payload = (await response.json()) as { reason?: string } & Record<string, unknown>
      if (!response.ok) {
        setRefusal(payload.reason ?? 'That did not work. Try again in a moment.')
        setPhase('idle')
        return
      }
      handleRef.current = await startTalking(
        payload as unknown as Parameters<typeof startTalking>[0],
        {
          onPhase: setPhase,
          onLine: record,
          onLevels: setLevels,
          onEnded: (reason) => {
            setEnding(reason)
            handleRef.current = null
          },
          onTrouble: setTrouble,
        },
      )
      setMuted(false)
    } catch (error) {
      // The most likely cause by far is a refused microphone, and telling a
      // visitor "NotAllowedError" tells them nothing they can act on.
      const detail = error instanceof Error ? error.message : ''
      setRefusal(
        /permission|denied|notallowed/i.test(detail)
          ? 'This needs your microphone. Allow it in your browser and start the call again.'
          : 'The call could not be started. Try again in a moment.',
      )
      await handleRef.current?.end().catch(() => {})
      handleRef.current = null
      setPhase('idle')
    }
  }, [code, language, record])

  const end = useCallback(async () => {
    // The handle reports its own ending, so this does not set one: one source
    // for "why did the call stop", whether it stopped here or over there.
    await handleRef.current?.end().catch(() => {})
    handleRef.current = null
  }, [])

  const inCall = inCallPhases.has(phase)
  const busy = phase === 'starting'

  return (
    <main className="mx-auto flex min-h-screen max-w-[860px] flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="text-center sm:text-left">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">
            Binghatti ambassador
          </h1>
          {/* The state chip stays: it is the one place a reduced-motion
              visitor and a screen reader both read the phase of the CALL, as
              opposed to the orb's label, which reads the turn. */}
          <span
            className={`border px-2.5 py-1 text-[11px] tracking-[0.12em] uppercase ${
              phase === 'live'
                ? 'border-brass-500 text-brass-400'
                : 'border-ink-700 text-ink-400'
            }`}
          >
            {label(phase)}
          </span>
        </div>
        <p className="mt-1.5 max-w-[74ch] text-[12px] leading-relaxed text-ink-500">
          A voice conversation with the ambassador, in your browser. It answers on the
          real inventory, and it will hand you to a person when a question needs one.
        </p>
      </header>

      {refusal ? (
        <p
          className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300"
          role="status"
        >
          {refusal}
        </p>
      ) : null}
      {trouble ? (
        <p className="border border-ink-700 px-5 py-3.5 text-[13px] text-ink-300" role="status">
          {trouble}
        </p>
      ) : null}
      {ending !== null ? (
        <p
          className={`border px-5 py-3.5 text-[13px] leading-relaxed text-ink-300 ${
            ending.kind === 'ended' ? 'border-ink-700' : 'border-warn-500/40'
          }`}
          role="status"
        >
          {ending.message}
          {lines.length > 0 ? ' The conversation stays below.' : ''}
        </p>
      ) : null}

      {inCall ? (
        <section className="flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => {
              const next = !muted
              setMuted(next)
              void handleRef.current?.setMuted(next)
            }}
            className="border border-ink-600 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 hover:border-brass-500 hover:text-brass-400"
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button
            type="button"
            onClick={() => void end()}
            className="border border-warn-500/50 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 hover:border-warn-500"
          >
            End call
          </button>
        </section>
      ) : (
        <form
          className="flex flex-wrap items-end justify-center gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (busy || code.trim() === '') return
            void start()
          }}
        >
          <div className="flex flex-col gap-1.5">
            <label
              className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
              htmlFor="talk-code"
            >
              Access code
            </label>
            <input
              id="talk-code"
              type="password"
              autoComplete="off"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="w-[22ch] border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
            />
          </div>
          {/* One language is not a choice, so it is not rendered as one: a
              select with a single option asks the visitor to make a decision
              that has already been made for them. It still says WHICH language,
              because a client who expected Arabic should find that out here
              rather than when the ambassador speaks. */}
          {choices.length > 1 ? (
            <div className="flex flex-col gap-1.5">
              <label
                className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
                htmlFor="talk-language"
              >
                Language
              </label>
              <select
                id="talk-language"
                value={language}
                onChange={(event) => setLanguage(event.target.value as LanguageCode)}
                className="border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
              >
                {choices.map((option) => (
                  <option key={option.code} value={option.code}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] tracking-[0.12em] text-ink-400 uppercase">
                Language
              </span>
              <p className="px-1 py-2.5 text-[13px] text-ink-300">
                {choices[0]?.label ?? 'English'}
              </p>
            </div>
          )}
          <button
            type="submit"
            disabled={busy || code.trim() === ''}
            className="border border-ink-600 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
          >
            {busy ? 'Starting' : ending !== null ? 'Start another call' : 'Start call'}
          </button>
        </form>
      )}

      {/* The orb is the centre of the page once a call is up: a visitor with
          nothing to look at cannot tell a thinking pause from a broken demo.
          Before the first call it is still shown, idle and dim, so the page is
          not a bare form either. */}
      <section className="flex flex-col items-center gap-8 py-2">
        <TalkOrb state={orb} level={orb === 'visitor' ? levels.visitor : levels.agent} name={name} />
        <TalkSubtitles
          lines={lines}
          name={name}
          idle={
            inCall
              ? 'Say hello whenever you are ready.'
              : ending !== null
                ? 'That call had nothing transcribed before it stopped.'
                : 'The conversation will appear here as it happens.'
          }
        />
        {/* The words are the VERBALISED form: the transcription streams carry
            what was spoken, so a price reads as words rather than digits
            (docs/09-). That is what the visitor heard, which is the honest
            thing to show even though a reader might expect the figures. */}
        <p className="max-w-[62ch] text-center text-[12px] leading-relaxed text-ink-600">
          Figures appear here the way they were spoken rather than as digits, because this
          is a record of the conversation rather than a quote.
        </p>
      </section>

      <footer className="mt-auto flex flex-col gap-2 border-t border-ink-800 pt-4">
        {/* The one honest sentence the scope asks for, naming what is missing
            rather than replaying a fixture into empty panels. */}
        <p className="max-w-[80ch] text-[12px] leading-relaxed text-ink-500">
          This page shows the conversation only. The latency meter, the guardrail and
          violation panels and the ambassador brief are not shown here: they carry
          unredacted internal records, so they stay on our own machine rather than being
          reproduced for you from a script.
        </p>
        <p className="text-[12px] text-ink-600">
          <Link className="hover:text-brass-400" href="/">
            Demo surface
          </Link>
        </p>
      </footer>
    </main>
  )
}

function label(phase: TalkPhase | 'idle' | 'starting'): string {
  switch (phase) {
    case 'live':
      return 'In call'
    case 'connecting':
    case 'starting':
      return 'Connecting'
    case 'reconnecting':
      return 'Reconnecting'
    case 'ended':
      return 'Call ended'
    default:
      return 'Ready'
  }
}
