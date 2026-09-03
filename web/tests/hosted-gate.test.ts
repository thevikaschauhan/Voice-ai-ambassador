// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The access gate, and the three refusals that hang off it.
 *
 * The direction under test is the one that matters: an UNSET gate is a CLOSED
 * gate. A misconfigured public service that lets everyone in front of metered
 * providers is worse than one that lets nobody in, and only the second failure
 * gets reported rather than billed.
 */

beforeEach(() => {
  vi.resetModules()
  delete process.env.DEMO_LANGUAGES
  delete process.env.DEMO_ACCESS_CODE
  delete process.env.DEMO_MAX_ROOMS
  delete process.env.AMBASSADOR_AGENT_DIR
})

afterEach(() => {
  delete process.env.DEMO_LANGUAGES
  delete process.env.DEMO_ACCESS_CODE
  delete process.env.DEMO_MAX_ROOMS
  delete process.env.AMBASSADOR_AGENT_DIR
})

describe('the access gate', () => {
  it('is closed when no code is configured, whatever is offered', async () => {
    const { checkAccessCode } = await import('@/lib/hosted')
    expect(checkAccessCode('anything')).toBe('closed')
    expect(checkAccessCode('')).toBe('closed')
    expect(checkAccessCode(undefined)).toBe('closed')
  })

  it('accepts the configured code and refuses everything else', async () => {
    process.env.DEMO_ACCESS_CODE = 'a-long-enough-code'
    const { checkAccessCode } = await import('@/lib/hosted')
    expect(checkAccessCode('a-long-enough-code')).toBe('ok')
    // Trimmed, because a code read down a phone line and pasted picks up
    // whitespace, and that is not a security decision.
    expect(checkAccessCode('  a-long-enough-code  ')).toBe('ok')
    expect(checkAccessCode('a-long-enough-cod')).toBe('refused')
    expect(checkAccessCode('A-LONG-ENOUGH-CODE')).toBe('refused')
    // A length mismatch must refuse rather than throw: timingSafeEqual throws
    // on unequal lengths, and an exception here would be a 500 instead of a
    // 403 - and would leak the length through the difference.
    expect(checkAccessCode('x')).toBe('refused')
    expect(checkAccessCode(`${'x'.repeat(200)}`)).toBe('refused')
    expect(checkAccessCode(42)).toBe('refused')
    expect(checkAccessCode(null)).toBe('refused')
  })

  it('treats the presence of a code as the hosted signal', async () => {
    const first = await import('@/lib/hosted')
    expect(first.hosted()).toBe(false)
    vi.resetModules()
    process.env.DEMO_ACCESS_CODE = 'a-long-enough-code'
    const second = await import('@/lib/hosted')
    expect(second.hosted()).toBe(true)
  })

  it('flags a code too short to be worth having, without refusing it', async () => {
    process.env.DEMO_ACCESS_CODE = 'short'
    const { accessCodeIsWeak, checkAccessCode } = await import('@/lib/hosted')
    expect(accessCodeIsWeak()).toBe(true)
    // Still works: this is an operator warning, not a second gate. Refusing a
    // configured code would fail closed in a way nobody could diagnose.
    expect(checkAccessCode('short')).toBe('ok')
  })
})

describe('the room cap', () => {
  it('falls back to its in-code default rather than to unlimited', async () => {
    const { maxRooms, DEFAULT_MAX_ROOMS } = await import('@/lib/hosted')
    expect(maxRooms()).toBe(DEFAULT_MAX_ROOMS)
  })

  it('reads a configured cap, and refuses to read nonsense as unlimited', async () => {
    for (const [value, expected] of [
      ['5', 5],
      ['1', 1],
      ['nonsense', null],
      ['0', null],
      ['-3', null],
      ['2.5', null],
      ['', null],
    ] as const) {
      vi.resetModules()
      process.env.DEMO_MAX_ROOMS = value
      const { maxRooms, DEFAULT_MAX_ROOMS } = await import('@/lib/hosted')
      expect(maxRooms()).toBe(expected ?? DEFAULT_MAX_ROOMS)
    }
  })
})

describe('text mode availability', () => {
  it('runs the real core whenever an agent is beside it, hosted or not', async () => {
    process.env.AMBASSADOR_AGENT_DIR = '/somewhere/agent'
    process.env.DEMO_ACCESS_CODE = 'a-long-enough-code'
    const { textModeAvailability } = await import('@/lib/textmode/availability')
    expect(textModeAvailability()).toBe('real')
  })

  it('keeps the laptop’s labelled replay when nobody is hosting', async () => {
    const { textModeAvailability } = await import('@/lib/textmode/availability')
    expect(textModeAvailability()).toBe('replay')
  })

  it('refuses on the hosted service, because a client cannot read a label they were never given', async () => {
    process.env.DEMO_ACCESS_CODE = 'a-long-enough-code'
    const { textModeAvailability, textModeRefused } = await import(
      '@/lib/textmode/availability'
    )
    expect(textModeAvailability()).toBe('refused')
    expect(textModeRefused()).toBe(true)
  })
})

describe('which languages a deployment offers', () => {
  it('offers all three when nothing says otherwise, so a laptop is unchanged', async () => {
    const { offeredLanguages } = await import('@/lib/hosted')
    expect(offeredLanguages()).toEqual(['en', 'ar', 'hi'])
  })

  it('narrows to what the variable names', async () => {
    process.env.DEMO_LANGUAGES = 'en'
    const { offeredLanguages } = await import('@/lib/hosted')
    expect(offeredLanguages()).toEqual(['en'])
  })

  it('reads a list, and tolerates the spacing and casing a human types', async () => {
    process.env.DEMO_LANGUAGES = ' EN , hi '
    const { offeredLanguages } = await import('@/lib/hosted')
    // Ordered by the product's own list, not by the order somebody typed them:
    // the picker's order should not be a property of an env var.
    expect(offeredLanguages()).toEqual(['en', 'hi'])
  })

  it('ignores a code that is not a language we speak', async () => {
    process.env.DEMO_LANGUAGES = 'en,fr'
    const { offeredLanguages } = await import('@/lib/hosted')
    expect(offeredLanguages()).toEqual(['en'])
  })

  it('falls back to ENGLISH ONLY when the variable is set but names nothing known', async () => {
    process.env.DEMO_LANGUAGES = 'eng'
    const { offeredLanguages } = await import('@/lib/hosted')
    // The direction is the point. Somebody who typed this meant to restrict;
    // re-opening Arabic and Hindi on a public URL because they mistyped would
    // do the opposite of what they asked, and do it silently.
    expect(offeredLanguages()).toEqual(['en'])
  })

  it('treats an empty value as unset rather than as "none"', async () => {
    process.env.DEMO_LANGUAGES = '   '
    const { offeredLanguages } = await import('@/lib/hosted')
    expect(offeredLanguages()).toEqual(['en', 'ar', 'hi'])
  })
})

describe('the offered check the route actually uses', () => {
  it('accepts an offered language and refuses one that is real but not on offer', async () => {
    process.env.DEMO_LANGUAGES = 'en'
    const { isOfferedLanguage } = await import('@/lib/livekit/talk')
    expect(isOfferedLanguage('en')).toBe(true)
    // Hindi is a language the worker can speak; this deployment does not offer
    // it, and the difference is the whole card.
    expect(isOfferedLanguage('hi')).toBe(false)
    expect(isOfferedLanguage('fr')).toBe(false)
    expect(isOfferedLanguage(undefined)).toBe(false)
  })

  it('accepts all three when nothing narrows them', async () => {
    const { isOfferedLanguage } = await import('@/lib/livekit/talk')
    for (const code of ['en', 'ar', 'hi']) expect(isOfferedLanguage(code)).toBe(true)
  })
})
