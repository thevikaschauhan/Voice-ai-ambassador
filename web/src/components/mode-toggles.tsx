'use client'

import type { ReplayScript } from '@/lib/session/scripts/types'
import type { GuardrailMode, PromptMode } from '@/lib/types'

interface ModeTogglesProps {
  promptMode: PromptMode
  guardrailMode: GuardrailMode
  script: ReplayScript
  onPromptMode: (mode: PromptMode) => void
  onGuardrailMode: (mode: GuardrailMode) => void
}

/**
 * The pair the defence-in-depth demo depends on. Not optional (docs/06-).
 *
 * They are two independent process variables, so they get two independent
 * controls - the tech lead will want to set them independently, and pairing
 * them into one switch would hide that `PROMPT_MODE=naive` alone is a
 * different claim from `GUARDRAIL_MODE=warn` alone.
 *
 * Both are read at session start, so changing one restarts the call rather
 * than mutating it mid-turn. That is the real constraint, not a shortcut.
 */
export function ModeToggles({
  promptMode,
  guardrailMode,
  script,
  onPromptMode,
  onGuardrailMode,
}: ModeTogglesProps) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <Segmented
          name="PROMPT_MODE"
          value={promptMode}
          options={[
            { value: 'ambassador', label: 'ambassador' },
            { value: 'naive', label: 'naive' },
          ]}
          onChange={onPromptMode}
        />
        <Segmented
          name="GUARDRAIL_MODE"
          value={guardrailMode}
          options={[
            { value: 'enforce', label: 'enforce' },
            { value: 'warn', label: 'warn' },
          ]}
          onChange={onGuardrailMode}
        />
      </div>
      <p className="max-w-[68ch] text-[12px] leading-relaxed text-ink-400">
        <span className="text-ink-200">{script.label}.</span> {script.note}
      </p>
    </div>
  )
}

function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
}: {
  name: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-[11px] tracking-[0.12em] text-ink-500 uppercase">
        {name}
      </legend>
      <div className="flex" role="group" aria-label={name}>
        {options.map((option, i) => {
          const selected = option.value === value
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(option.value)}
              className={`border px-4 py-1.5 text-[12px] tracking-wide ${
                i > 0 ? '-ml-px' : ''
              } ${
                selected
                  ? 'border-brass-500 text-brass-400'
                  : 'border-ink-700 text-ink-400 hover:border-ink-500'
              }`}
            >
              {option.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}
