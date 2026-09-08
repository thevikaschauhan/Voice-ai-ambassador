import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CallPanel } from '@/components/call-panel'
import type { LanguageReadiness } from '@/lib/readiness'
import { initialState } from '@/lib/session/state'

/**
 * The public demo page's language selector, and the caption under it.
 *
 * Two separate claims, both broken by the language-switch commit (3ab4928):
 *
 * 1. The selector offers the languages a call can be OPENED in. The seven codes
 *    the runtime gained are mid-call switch TARGETS, and a switch target has no
 *    business on this page - `readiness.ts` widened its list to all ten, so the
 *    page grew seven permanently-greyed buttons.
 * 2. The caption names the unavailable languages. It was a hardcoded "Arabic
 *    and Hindi", true only while exactly those two lacked copy. It has to be
 *    DERIVED, so the test that matters is the one where the set is different -
 *    a test that only checks today's wording passes against the hardcoded
 *    string and proves nothing.
 *
 * jim, review findings P1-B (ryan) and P2-4, card task-lsfix-web.
 */

const { readFile } = vi.hoisted(() => ({ readFile: vi.fn() }))
vi.mock('node:fs/promises', () => ({ readFile, default: { readFile } }))

function panel(languages: readonly LanguageReadiness[]) {
  render(
    <CallPanel
      state={initialState()}
      running={false}
      provenance="fixture"
      languages={languages}
      onStart={() => {}}
      onEnd={() => {}}
    />,
  )
  return within(screen.getByRole('group', { name: /call language/i }))
}

type Scope = ReturnType<typeof panel>

function optionNames(scope: Scope): string[] {
  return scope.getAllByRole('button').map((button) => button.textContent?.trim() ?? '')
}

afterEach(cleanup)

describe('the language selector', () => {
  it('offers exactly the three opening languages, English live and the other two greyed', () => {
    const scope = panel([
      { language: 'en', ready: true },
      { language: 'ar', ready: false },
      { language: 'hi', ready: false },
    ])

    expect(optionNames(scope)).toEqual(['English', 'Arabic', 'Hindi'])
    expect(scope.getByRole('button', { name: 'English' })).toBeEnabled()
    expect(scope.getByRole('button', { name: 'Arabic' })).toBeDisabled()
    expect(scope.getByRole('button', { name: 'Hindi' })).toBeDisabled()
  })

  it('names both unavailable languages in the caption', () => {
    const scope = panel([
      { language: 'en', ready: true },
      { language: 'ar', ready: false },
      { language: 'hi', ready: false },
    ])

    expect(scope.getByText(/Arabic and Hindi are unavailable/)).toBeInTheDocument()
  })

  it('drops a language from the caption once its disclosure is authored', () => {
    // The assertion a hardcoded string cannot pass: Arabic has copy now, so the
    // caption must name Hindi ALONE and must not still say "Arabic".
    const scope = panel([
      { language: 'en', ready: true },
      { language: 'ar', ready: true },
      { language: 'hi', ready: false },
    ])

    const caption = scope.getByText(/unavailable/)
    expect(caption).toHaveTextContent(/Hindi is unavailable/)
    expect(caption).not.toHaveTextContent(/Arabic/)
    expect(scope.getByRole('button', { name: 'Arabic' })).toBeEnabled()
  })

  it('says nothing about availability once every opening language is ready', () => {
    const scope = panel([
      { language: 'en', ready: true },
      { language: 'ar', ready: true },
      { language: 'hi', ready: true },
    ])

    expect(optionNames(scope)).toEqual(['English', 'Arabic', 'Hindi'])
    expect(scope.queryByText(/unavailable/)).toBeNull()
  })
})

describe('which languages the page can open a call in', () => {
  beforeEach(() => {
    readFile.mockReset()
  })

  async function readinessFor(disclosures: string) {
    readFile.mockResolvedValue(disclosures)
    const { loadLanguageReadiness } = await import('@/lib/readiness')
    return loadLanguageReadiness()
  }

  it('reads the three opening languages out of the disclosure file', async () => {
    const readiness = await readinessFor('en: >\n  We are an AI.\nar: ""\nhi: ""\n')

    expect(readiness).toEqual([
      { language: 'en', ready: true },
      { language: 'ar', ready: false },
      { language: 'hi', ready: false },
    ])
  })

  it('never offers a mid-call switch target, even one with authored copy', async () => {
    // Russian is a switch target, not an opening language. Certifying it must
    // not put a fourth button on the public page. The list is asserted PRESENT
    // and correct first, so "no Russian" cannot pass on an empty result.
    const readiness = await readinessFor(
      'en: >\n  We are an AI.\nar: ""\nhi: ""\nru: >\n  A Russian disclosure.\n',
    )

    expect(readiness.map((entry) => entry.language)).toEqual(['en', 'ar', 'hi'])
    expect(readiness.find((entry) => entry.language === 'en')?.ready).toBe(true)
  })
})
