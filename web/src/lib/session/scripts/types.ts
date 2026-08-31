import type { GuardrailMode, PromptMode } from '@/lib/types'
import type { SessionInput } from '@/lib/session/events'

/** `after` is the delay in milliseconds from the previous step. */
export interface ReplayStep {
  after: number
  input: SessionInput
}

export interface ReplayScript {
  id: string
  promptMode: PromptMode
  guardrailMode: GuardrailMode
  /** What this pairing is called on screen. Honest labels only (docs/03-). */
  label: string
  /** One sentence on what the pairing demonstrates. */
  note: string
  steps: ReplayStep[]
}

export function modeKey(prompt: PromptMode, guardrail: GuardrailMode): string {
  return `${prompt}+${guardrail}`
}
