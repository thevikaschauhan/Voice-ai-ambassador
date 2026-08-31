import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TextMode } from '@/components/text-mode'
import { aed } from '@/lib/format'
import { replayTextCore } from '@/lib/textmode/core'
import { PROJECTS, project } from './fixtures'

/**
 * The venue plan B, asserted on what the room reads.
 *
 * `fetch` is routed through the same `TextCore` the route handler uses, so
 * these exercise the real request shape rather than a stubbed reply. The one
 * thing deliberately faked is the network, because the failure test needs it
 * to break.
 */

const core = replayTextCore()

function routeFetch(): typeof fetch {
  return (async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as {
      sessionId: string
      turnIndex: number
      text: string
    }
    const events = await core.turn(body)
    return {
      ok: true,
      status: 200,
      json: async () => ({ events }),
    } as Response
  }) as typeof fetch
}

async function ask(question: string) {
  const input = screen.getByLabelText('Message the ambassador')
  await act(async () => {
    await userEvent.type(input, question)
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
  })
}

beforeEach(() => {
  render(<TextMode projects={PROJECTS} />)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('text mode', () => {
  it('answers a price question with the figure that is in inventory', async () => {
    vi.stubGlobal('fetch', routeFetch())
    await ask('what does the Skyrise start at')

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).getByText('what does the Skyrise start at')).toBeInTheDocument()
    expect(
      within(transcript).getByText(
        'Binghatti Skyrise in Business Bay starts from nine hundred and eighty-five thousand dirhams.',
      ),
    ).toBeInTheDocument()
  })

  it('carries the shortlist into the ambassador view with its derived figures', async () => {
    vi.stubGlobal('fetch', routeFetch())
    await ask('what would I pay upfront')

    const skyrise = project('binghatti-skyrise')
    const booking = skyrise.payment_plan![0]
    const derived = Math.round(skyrise.price_from_aed! * (booking.pct / 100))

    const ambassador = screen.getByLabelText('Ambassador view')
    expect(within(ambassador).getByText(skyrise.name)).toBeInTheDocument()
    expect(within(ambassador).getByText(aed(derived))).toBeInTheDocument()
  })

  it('refuses a branded price and hands the buyer to a human', async () => {
    vi.stubGlobal('fetch', routeFetch())
    await ask('how much is a Bugatti Residences penthouse')

    expect(
      within(screen.getByLabelText('Transcript')).getByText(/pricing there is on enquiry/),
    ).toBeInTheDocument()
    expect(
      within(screen.getByLabelText('Ambassador view')).getByText('Handed to a human'),
    ).toBeInTheDocument()
  })

  it('answers a compound question with the refusal, not another project’s plan', async () => {
    vi.stubGlobal('fetch', routeFetch())
    await ask('what would I pay upfront on the Bugatti')

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).getByText(/pricing there is on enquiry/)).toBeInTheDocument()
    expect(within(transcript).queryByText(/booking payment is twenty percent/)).not.toBeInTheDocument()
  })

  it('refuses rather than guesses when the question is outside inventory', async () => {
    vi.stubGlobal('fetch', routeFetch())
    await ask('what about Binghatti Mirage in Al Barsha')

    const transcript = screen.getByLabelText('Transcript')
    expect(
      within(transcript).getByText(/I do not want to tell you something I cannot confirm/),
    ).toBeInTheDocument()
    expect(within(transcript).queryByText(/AED/)).not.toBeInTheDocument()
  })

  it('never ends a turn in silence when the core cannot be reached', async () => {
    vi.stubGlobal(
      'fetch',
      (async () => {
        throw new Error('network down')
      }) as unknown as typeof fetch,
    )
    await ask('what does the Skyrise start at')

    const transcript = screen.getByLabelText('Transcript')
    expect(
      within(transcript).getByText(
        'I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/composed handover stood in/)).toBeInTheDocument()
  })
})
