import { describe, expect, it } from 'vitest'
import type { AgentEvent } from '@/lib/session/events'
import { initialState, reduce } from '@/lib/session/state'

describe('the session-start event contract', () => {
  it('reads the flat shape and tolerates the legacy nested-config shape', () => {
    const flat: AgentEvent = {
      event: 'session_start',
      session: 'sess-flat',
      model: 'qwen/qwen3.7-flash',
      language: 'hi',
      prompt_mode: 'naive',
      guardrail_mode: 'warn',
      inventory_version: '0123456789ab',
    }

    expect(reduce(initialState(), flat)).toMatchObject({
      sessionId: 'sess-flat',
      connection: 'live',
      model: 'qwen/qwen3.7-flash',
      language: 'hi',
      promptMode: 'naive',
      guardrailMode: 'warn',
      inventoryVersion: '0123456789ab',
    })

    const nested = {
      event: 'session_start',
      session: 'sess-nested',
      config: {
        llm_model: 'qwen/qwen3.7-flash',
        language: 'ar',
        prompt_mode: 'ambassador',
        guardrail_mode: 'enforce',
      },
    } as unknown as AgentEvent

    expect(() => reduce(initialState(), nested)).not.toThrow()
    expect(reduce(initialState(), nested).connection).toBe('live')
  })
})
