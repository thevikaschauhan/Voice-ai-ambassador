import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TalkCall } from '@/components/talk-call'
import { ambassadorNames } from './fixtures'

/**
 * The client's own button (task-web-silent-disabled-family).
 *
 * `talk-page.test.tsx` presses Start call only to get past it and into a live
 * call; nothing anywhere asserted what the button does BEFORE a code is typed,
 * which is the state every visitor arrives in.
 *
 * Reproduced on a production build before writing this:
 *
 *   /talk on arrival -> Start call: disabled=true, opacity 0.4
 *   pressing it      -> no request at all
 *   the Enter key    -> no request at all
 *
 * AND THE PAGE SAYS READY WHILE IT DOES SO - twice, the header badge and the
 * line under the orb beside Jane's name. That is what makes this the worst of
 * the three: the admin pages promised nothing, and this one tells the client
 * the ambassador is ready to talk while the only control is inert and silent.
 * READY stays, because it describes the SERVICE and it is true; what was
 * missing is the form answering for itself.
 *
 * THE RULE (god, 2026-09-07): a control is never disabled because an input is
 * missing; pressing it names the missing input in the status region; disabled
 * only while a request is in flight or when the server has already refused.
 */

vi.mock('@/lib/talk/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/talk/session')>()
  return {
    ...actual,
    startTalking: vi.fn(async () => ({ end: async () => {}, setMuted: async () => {} })),
  }
})

function stubFetch() {
  const sent: string[] = []
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL) => {
      sent.push(String(input))
      return new Response(
        JSON.stringify({
          url: 'wss://example.livekit.cloud',
          token: 'a-token',
          room: 'demo-abc',
          identity: 'visitor-1234',
          language: 'en',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )
    }) as typeof fetch,
  )
  return sent
}

beforeEach(() => {
  vi.restoreAllMocks()
  render(<TalkCall names={ambassadorNames({ en: 'Jane', ar: '', hi: '' })} />)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the client’s Start call button', () => {
  it('is pressable when the visitor arrives, before anything is typed', () => {
    expect(screen.getByRole('button', { name: 'Start call' })).toBeEnabled()
  })

  it('names the missing access code when pressed empty, and mints no room', async () => {
    const sent = stubFetch()
    await userEvent.click(screen.getByRole('button', { name: 'Start call' }))

    expect(await screen.findByText(/enter the access code/i)).toBeInTheDocument()
    // Never spends a room-token attempt on a press that carried nothing.
    expect(sent).toHaveLength(0)
  })

  it('answers the Enter key the same way as the button', async () => {
    const sent = stubFetch()
    await userEvent.click(screen.getByLabelText('Access code'))
    await userEvent.keyboard('{Enter}')

    // The guard lived in TWO places - the button's `disabled` and the submit
    // handler's early return - so fixing only the button would have left the
    // keyboard path silent: the same defect wearing a different hat.
    expect(await screen.findByText(/enter the access code/i)).toBeInTheDocument()
    expect(sent).toHaveLength(0)
  })

  it('still starts the call when there is a code', async () => {
    const sent = stubFetch()
    await userEvent.type(screen.getByLabelText('Access code'), 'the-code')
    await userEvent.click(screen.getByRole('button', { name: 'Start call' }))

    expect(sent).toEqual(['/api/talk'])
  })

  it('never puts the access code on screen', async () => {
    stubFetch()
    await userEvent.type(screen.getByLabelText('Access code'), 'hunter2hunter2')
    await userEvent.clear(screen.getByLabelText('Access code'))
    await userEvent.click(screen.getByRole('button', { name: 'Start call' }))

    // The whole page, not the message: a secret leaks by being anywhere on
    // screen, not by being in the string somebody happened to read.
    expect(document.body.textContent ?? '').not.toMatch(/hunter2/i)
  })
})
