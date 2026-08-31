'use client'

import { num } from '@/lib/format'
import type { SessionState } from '@/lib/session/state'

/**
 * Failure states, designed rather than default (issue #9).
 *
 * Each of these is a real condition the agent can report, and each says what
 * it means for the call rather than showing a code. None of them is an
 * exception surfaced raw: a buyer-facing system that prints a stack trace on
 * stage has already lost the room.
 */
export function SessionBanner({ state }: { state: SessionState }) {
  if (state.connection === 'lost') {
    return (
      <Banner tone="flag" title="Connection lost">
        The room has dropped. LiveKit reconnects on its own; the agent finalises the turn
        in flight and the last spoken chunk audits as incomplete rather than as delivered.
      </Banner>
    )
  }

  if (state.error !== null) {
    return (
      <Banner tone="flag" title="Session error">
        The session reported an error and the call cannot continue. The buyer heard the
        composed handover line, not silence.
      </Banner>
    )
  }

  if (state.uncertifiedFallback) {
    return (
      <Banner tone="warn" title="Opened in English as a fallback">
        The requested language has no native-authored disclosure, so the call opened in
        English and the event stream is marked <code>uncertified_fallback</code>. This is
        graceful degradation, not a shipped language.
      </Banner>
    )
  }

  if (state.droppedEvents > 0) {
    return (
      <Banner tone="warn" title="Event log fell behind">
        {num(state.droppedEvents)} event
        {state.droppedEvents === 1 ? '' : 's'} dropped under backpressure. The voice path
        is never blocked by the audit, so the oldest lines lose - and the count is
        reported rather than the drop being silent.
      </Banner>
    )
  }

  return null
}

function Banner({
  tone,
  title,
  children,
}: {
  tone: 'flag' | 'warn'
  title: string
  children: React.ReactNode
}) {
  const border = tone === 'flag' ? 'border-flag-500/40' : 'border-warn-500/40'
  const text = tone === 'flag' ? 'text-flag-400' : 'text-warn-500'
  return (
    <div className={`border ${border} px-5 py-3.5`} role="status">
      <p className={`text-[12px] tracking-[0.12em] uppercase ${text}`}>{title}</p>
      <p className="mt-1.5 max-w-[80ch] text-[13px] leading-relaxed text-ink-300">
        {children}
      </p>
    </div>
  )
}
