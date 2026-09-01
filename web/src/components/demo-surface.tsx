'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { AmbassadorView } from '@/components/ambassador-view'
import { CallPanel } from '@/components/call-panel'
import { LatencyMeter } from '@/components/latency-meter'
import { ModeToggles } from '@/components/mode-toggles'
import { SessionBanner } from '@/components/session-banner'
import { TranscriptRail } from '@/components/transcript-rail'
import { useSession } from '@/hooks/use-session'
import type { LanguageReadiness } from '@/lib/readiness'
import { liveSource } from '@/lib/session/live-source'
import { scriptFor } from '@/lib/session/scripts'
import type { ReplayScript } from '@/lib/session/scripts/types'
import { roomSource } from '@/lib/session/room-source'
import { combine, replaySource } from '@/lib/session/source'
import type { Provenance } from '@/lib/session/source'
import type { GuardrailMode, Project, PromptMode } from '@/lib/types'

interface DemoSurfaceProps {
  projects: readonly Project[]
  languages: readonly LanguageReadiness[]
  /** True when the agent left a handshake for us. Decided on the server. */
  live: boolean
  /** Why not, when it is not. Shown rather than swallowed. */
  liveReason?: string
  /** True when this server has LiveKit credentials to mint a viewer token. */
  room: boolean
}

/**
 * Owns the mode selection and the session key, and nothing else.
 *
 * The session lives one level down so that a change of source or of mode
 * remounts it: both modes are process configuration read at session start, so
 * a call cannot be re-moded in flight, and keying on it is what enforces that
 * rather than a comment asking people to remember.
 */
export function DemoSurface({
  projects,
  languages,
  live,
  liveReason,
  room,
}: DemoSurfaceProps) {
  const [promptMode, setPromptMode] = useState<PromptMode>('ambassador')
  const [guardrailMode, setGuardrailMode] = useState<GuardrailMode>('enforce')
  const script = scriptFor(promptMode, guardrailMode)
  const provenance: Provenance = live ? 'live' : 'replay'

  return (
    <CallSession
      key={`${provenance}:${script.id}`}
      script={script}
      provenance={provenance}
      projects={projects}
      languages={languages}
      liveReason={liveReason}
      room={room}
      onPromptMode={setPromptMode}
      onGuardrailMode={setGuardrailMode}
    />
  )
}

function CallSession({
  script,
  provenance,
  projects,
  languages,
  liveReason,
  room,
  onPromptMode,
  onGuardrailMode,
}: {
  script: ReplayScript
  provenance: Provenance
  projects: readonly Project[]
  languages: readonly LanguageReadiness[]
  liveReason?: string
  room: boolean
  onPromptMode: (mode: PromptMode) => void
  onGuardrailMode: (mode: GuardrailMode) => void
}) {
  const live = provenance === 'live'
  // Two feeds when a room is available: the bridge for what was said and
  // decided, the room for amplitude and who is talking. The bridge stops
  // inferring the latter when the room is there to measure it.
  const source = useMemo(() => {
    if (!live) return replaySource(script)
    const events = liveSource({ deriveTransport: !room })
    return room ? combine(events, roomSource()) : events
  }, [live, room, script])
  // A live session reports its own modes in `session_start`; a replay is
  // configured by the toggles, so it is seeded with what the script records.
  const { state, status, start, stop } = useSession(
    source,
    live ? {} : { promptMode: script.promptMode, guardrailMode: script.guardrailMode },
  )
  const running = status === 'running'

  // Live: the pairing on screen is the agent's, read back from its own
  // session_start, and the controls are inert because nothing here can change a
  // process that already started. A control that looks live and does nothing is
  // worse than one that says it cannot.
  const shown = live ? scriptFor(state.promptMode, state.guardrailMode) : script

  return (
    <main className="mx-auto flex min-h-screen max-w-[1680px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="space-y-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">
                Binghatti ambassador
              </h1>
              <ProvenanceChip provenance={provenance} />
            </div>
            <p className="mt-1.5 max-w-[76ch] text-[12px] leading-relaxed text-ink-500">
              {live
                ? 'Attached to a running agent. The page makes no model calls: events reach it from this server, which holds the only credential.'
                : `Replay fixture, not a call. ${liveReason ?? 'No agent is running.'}`}
            </p>
          </div>
          <nav className="flex items-center gap-5 text-[12px]">
            <Link className="text-ink-400 hover:text-brass-400" href="/text">
              Text mode
            </Link>
            <Link className="text-ink-400 hover:text-brass-400" href="/states">
              Designed states
            </Link>
          </nav>
        </div>
        <ModeToggles
          promptMode={live ? state.promptMode : script.promptMode}
          guardrailMode={live ? state.guardrailMode : script.guardrailMode}
          script={shown}
          readOnly={live}
          onPromptMode={onPromptMode}
          onGuardrailMode={onGuardrailMode}
        />
      </header>

      <SessionBanner state={state} />

      <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-2 lg:items-start">
        <div className="flex min-h-0 flex-col gap-6">
          <CallPanel
            state={state}
            running={running}
            provenance={provenance}
            languages={languages}
            onStart={start}
            onEnd={stop}
          />
          <TranscriptRail turns={state.turns} />
        </div>
        <div className="flex min-h-0 flex-col gap-6">
          <AmbassadorView state={state} projects={projects} />
          <LatencyMeter turns={state.turns} />
        </div>
      </div>
    </main>
  )
}

function ProvenanceChip({ provenance }: { provenance: Provenance }) {
  const live = provenance === 'live'
  return (
    <span
      className={`border px-2.5 py-1 text-[11px] tracking-[0.12em] uppercase ${
        live ? 'border-brass-500 text-brass-400' : 'border-ink-700 text-ink-400'
      }`}
    >
      {live ? 'Live agent' : 'Replay'}
    </span>
  )
}
