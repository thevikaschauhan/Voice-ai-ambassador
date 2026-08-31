'use client'

import Link from 'next/link'
import { useState } from 'react'
import { AmbassadorView } from '@/components/ambassador-view'
import { CallPanel } from '@/components/call-panel'
import { LatencyMeter } from '@/components/latency-meter'
import { ModeToggles } from '@/components/mode-toggles'
import { SessionBanner } from '@/components/session-banner'
import { TranscriptRail } from '@/components/transcript-rail'
import { useSession } from '@/hooks/use-session'
import type { LanguageReadiness } from '@/lib/readiness'
import { scriptFor } from '@/lib/session/scripts'
import type { ReplayScript } from '@/lib/session/scripts/types'
import type { GuardrailMode, Project, PromptMode } from '@/lib/types'

interface DemoSurfaceProps {
  projects: readonly Project[]
  languages: readonly LanguageReadiness[]
}

export function DemoSurface({ projects, languages }: DemoSurfaceProps) {
  const [promptMode, setPromptMode] = useState<PromptMode>('ambassador')
  const [guardrailMode, setGuardrailMode] = useState<GuardrailMode>('enforce')
  const script = scriptFor(promptMode, guardrailMode)

  return (
    <main className="mx-auto flex min-h-screen max-w-[1680px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="space-y-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
          <div>
            <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">
              Binghatti ambassador
            </h1>
            <p className="mt-1.5 text-[12px] text-ink-500">
              Demo surface. The page makes no model calls and opens no microphone.
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
          promptMode={promptMode}
          guardrailMode={guardrailMode}
          script={script}
          onPromptMode={setPromptMode}
          onGuardrailMode={setGuardrailMode}
        />
      </header>

      {/* A mode change is a session change, so the call restarts rather than
          mutating mid-flight. Keying on the script id is what enforces it. */}
      <CallSession
        key={script.id}
        script={script}
        projects={projects}
        languages={languages}
      />
    </main>
  )
}

function CallSession({
  script,
  projects,
  languages,
}: {
  script: ReplayScript
  projects: readonly Project[]
  languages: readonly LanguageReadiness[]
}) {
  const { state, status, start, stop } = useSession(script)
  const running = status === 'running'

  return (
    <>
      <SessionBanner state={state} />
      <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-2 lg:items-start">
        <div className="flex min-h-0 flex-col gap-6">
          <CallPanel
            state={state}
            running={running}
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
    </>
  )
}
