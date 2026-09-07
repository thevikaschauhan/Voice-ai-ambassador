import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'

/**
 * The admin door (task-web-admin-signin-cta).
 *
 * This component had NO test file until now, which is how the same pattern
 * shipped twice: `#147` fixed the knowledge intake's silent disabled button,
 * and this one - the first control anybody meets on `/admin` - had it too.
 *
 * Reproduced in a production build before writing any of this:
 *
 *   on arrival, empty field -> Sign in: disabled=true, opacity 0.4,
 *                              role=status region EMPTY
 *   pressing it             -> no request is made at all
 *
 * A DISABLED CONTROL IS NOT A MESSAGE (the rule from #147). It takes away the
 * one thing a visitor can press to find out what is wrong, and on a login
 * screen the visitor has nothing else to go on: there is no list, no draft,
 * nothing else on the page to explain the state.
 *
 * THE SECOND RULE HERE IS THE ACCESS CODE ITSELF. This form handles a secret,
 * so a message that reported its length, shape, or any part of its value would
 * be a worse defect than the one being fixed. The empty-press answer says what
 * to do and nothing about what was typed.
 */

/**
 * Imported inside each case with a variable specifier and `@vite-ignore`, the
 * house pattern: a literal dynamic import resolves at transform time and fails
 * the FILE to load, which reports "no tests" and gives a RED commit nothing to
 * count.
 */
async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

async function renderShell() {
  const { AdminShell } = (await load('@/components/admin-shell')) as unknown as {
    AdminShell: (p: { configured: boolean }) => ReactElement
  }
  // `signedIn` is gone: /admin's only call site always passed false, so the
  // signed-in half was unreachable code carrying a second site nav.
  return render(<AdminShell configured />)
}

function stubFetch(status = 204) {
  const sent: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push({
        url: String(input),
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body,
      })
      return new Response(status === 204 ? null : JSON.stringify({ error: 'no' }), { status })
    }) as typeof fetch,
  )
  return sent
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the admin door', () => {
  it('offers a pressable control before anything is typed', async () => {
    await renderShell()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeEnabled()
  })

  it('says what is missing when it is pressed empty, and sends nothing', async () => {
    const sent = stubFetch()
    await renderShell()
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    // Queried by its TEXT and then checked to be in a live region, rather
    // than by `getByRole('status')` alone. Astryx's Button renders its own
    // role=status live region for the "Loading" announcement, so a bare role
    // query became ambiguous the moment this form was restyled
    // (task-web-admin-astryx-shell). The claim is unchanged and slightly
    // stronger: THIS sentence is the one in a live region.
    const status = (await screen.findByText(/enter the access code/i)).closest(
      '[role="status"]',
    )
    expect(status).not.toBeNull()
    // An empty code is not a wrong code: sending it would spend one of the
    // rate limiter's attempts on a press that carried nothing.
    expect(sent).toHaveLength(0)
  })

  it('still posts the code once when there is one', async () => {
    const sent = stubFetch()
    await renderShell()
    await userEvent.type(screen.getByLabelText(/access code/i), 'an-admin-code-long-enough')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(sent).toHaveLength(1)
    expect(sent[0].url).toBe('/api/admin/login')
    expect(sent[0].body).toEqual({ code: 'an-admin-code-long-enough' })
  })

  it('never says anything about the code that was typed', async () => {
    stubFetch()
    await renderShell()
    await userEvent.type(screen.getByLabelText(/access code/i), 'hunter2hunter2')
    await userEvent.clear(screen.getByLabelText(/access code/i))
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    // The whole rendered page, not just the message: a secret leaks by being
    // anywhere on screen, not by being in the field somebody looked at.
    const shown = document.body.textContent ?? ''
    expect(shown).not.toMatch(/hunter2/i)
    expect(shown).not.toMatch(/\b14\b|characters|too short|length/i)
  })
})
