'use client'

import { Panel } from '@/components/panel'
import { Waveform } from '@/components/waveform'
import type { LanguageReadiness } from '@/lib/readiness'
import type { SessionState } from '@/lib/session/state'
import type { Language } from '@/lib/types'

const LANGUAGE_NAMES: Record<Language, string> = {
  en: 'English',
  ar: 'Arabic',
  hi: 'Hindi',
}

interface CallPanelProps {
  state: SessionState
  running: boolean
  languages: readonly LanguageReadiness[]
  onStart: () => void
  onEnd: () => void
}

export function CallPanel({ state, running, languages, onStart, onEnd }: CallPanelProps) {
  const speaking = state.buyerSpeaking || state.agentSpeaking
  const speaker = state.buyerSpeaking ? 'Buyer' : state.agentSpeaking ? 'Ambassador' : 'Nobody'

  return (
    <Panel title="Call" audience="Everyone" action={<ConnectionBadge state={state} />}>
      <div className="space-y-5">
        <Waveform levels={state.levels} active={speaking} label={`${speaker} audio`} />

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <Indicator on={state.buyerSpeaking} label="Buyer speaking" />
          <Indicator on={state.agentSpeaking} label="Ambassador speaking" />
          <Indicator on={state.bargeIn} label="Barge-in" flag />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={running ? onEnd : onStart}
            className="border border-ink-600 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 transition-colors hover:border-brass-500 hover:text-brass-400"
          >
            {running ? 'End call' : 'Start call'}
          </button>
          <span className="text-[12px] text-ink-500">
            Replay fixture. No microphone is opened and no provider is called from this
            page.
          </span>
        </div>

        <fieldset className="border-t border-ink-850 pt-4">
          <legend className="sr-only">Call language</legend>
          <p className="mb-2.5 text-[12px] tracking-wide text-ink-500">Language</p>
          <div className="flex flex-wrap gap-2">
            {languages.map(({ language, ready }) => {
              const selected = state.language === language
              return (
                <button
                  key={language}
                  type="button"
                  disabled={!ready}
                  aria-pressed={selected}
                  title={
                    ready
                      ? undefined
                      : 'No native-authored disclosure in data/disclosures.yaml, so a call cannot open in this language'
                  }
                  className={`border px-3.5 py-1.5 text-[12px] tracking-wide ${
                    selected
                      ? 'border-brass-500 text-brass-400'
                      : 'border-ink-700 text-ink-300'
                  } ${ready ? 'hover:border-ink-500' : 'cursor-not-allowed opacity-40'}`}
                >
                  {LANGUAGE_NAMES[language]}
                </button>
              )
            })}
          </div>
          {languages.some((l) => !l.ready) ? (
            <p className="mt-3 text-[12px] leading-relaxed text-ink-500">
              Arabic and Hindi are unavailable because neither has native-authored
              disclosure copy in <code className="text-ink-400">data/disclosures.yaml</code>.
              The agent refuses to open a call in a language it cannot disclose itself in,
              so readiness is a state of the repository rather than a setting here.
            </p>
          ) : null}
        </fieldset>

        {state.disclosure ? (
          <div className="border-l border-brass-600 pl-4">
            <p className="text-[12px] tracking-wide text-ink-500">
              Opening disclosure, spoken before the first turn
            </p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-ink-300">
              {state.disclosure}
            </p>
          </div>
        ) : null}
      </div>
    </Panel>
  )
}

function ConnectionBadge({ state }: { state: SessionState }) {
  const label =
    state.connection === 'live'
      ? 'Live'
      : state.connection === 'connecting'
        ? 'Connecting'
        : state.connection === 'ended'
          ? 'Ended'
          : state.connection === 'lost'
            ? 'Connection lost'
            : 'Idle'
  const live = state.connection === 'live'
  const lost = state.connection === 'lost'
  return (
    <span className="flex items-center gap-2 text-[11px] tracking-[0.12em] text-ink-400 uppercase">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 ${
          lost ? 'bg-flag-500' : live ? 'live-dot bg-brass-400' : 'bg-ink-600'
        }`}
      />
      {label}
    </span>
  )
}

function Indicator({ on, label, flag = false }: { on: boolean; label: string; flag?: boolean }) {
  return (
    <span className="flex items-center gap-2 text-[12px]">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 ${on ? (flag ? 'bg-flag-500' : 'bg-brass-400') : 'bg-ink-700'}`}
      />
      <span className={on ? 'text-ink-100' : 'text-ink-500'}>{label}</span>
      <span className="sr-only">{on ? 'active' : 'inactive'}</span>
    </span>
  )
}
