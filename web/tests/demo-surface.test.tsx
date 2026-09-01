import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DemoSurface } from '@/components/demo-surface'
import { aed } from '@/lib/format'
import { LANGUAGES, PROJECTS, project } from './fixtures'

/**
 * These assert what the ROOM sees, across turns, not what a function returned.
 *
 * The repository learned this the hard way (AGENTS.md, 2026-08-31): eight
 * defects shipped green under tests that asserted on the objects a state
 * machine returned, and not one asserted on what the user received. So every
 * expectation below is a query against rendered text.
 */

function play(ms: number): Promise<void> {
  return act(async () => {
    vi.advanceTimersByTime(ms)
  })
}

function renderSurface() {
  render(<DemoSurface projects={PROJECTS} languages={LANGUAGES} live={false} room={false} />)
}

async function startCall() {
  await act(async () => {
    screen.getByRole('button', { name: 'Start call' }).click()
  })
}

/** Long enough for any of the four replays to run to completion. */
const WHOLE_CALL = 60_000

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('the demo path, ambassador prompt with the guardrail enforcing', () => {
  it('speaks the deterministic budget confirmation before the model ever runs', async () => {
    renderSurface()
    await startCall()
    await play(5_000)

    const transcript = screen.getByLabelText('Transcript')
    expect(
      within(transcript).getByText(/My budget is around two million/),
    ).toBeInTheDocument()
    expect(
      within(transcript).getByText('Two million - is that in dirhams or in rupees?'),
    ).toBeInTheDocument()
    expect(
      within(transcript).getByText(/budget policy took this turn, so the model never ran/),
    ).toBeInTheDocument()
  })

  it('blocks a wrong price, regenerates, and speaks the figure that is in inventory', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const transcript = screen.getByLabelText('Transcript')

    // What the model produced, and the fact that it never reached the buyer.
    expect(
      within(transcript).getByText(
        'Binghatti Skyrise in Business Bay starts from AED 950,000.',
      ),
    ).toBeInTheDocument()
    expect(within(transcript).getByText('Blocked before synthesis')).toBeInTheDocument()
    expect(
      within(transcript).getByText(/figure 950000\.0 is not in the allowed set/),
    ).toBeInTheDocument()

    // What the buyer actually heard, verbalised, with the figure that is real.
    expect(
      within(transcript).getByText(
        'Binghatti Skyrise in Business Bay starts from nine hundred and eighty-five thousand dirhams.',
      ),
    ).toBeInTheDocument()
    expect(
      within(transcript).getByText(/One repair retry was spent/),
    ).toBeInTheDocument()
  })

  it('answers the payment question from a derived figure and audits the barge-in', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const transcript = screen.getByLabelText('Transcript')
    expect(
      within(transcript).getByText(
        'The booking payment is twenty percent, which is one hundred and ninety-seven thousand dirhams.',
      ),
    ).toBeInTheDocument()
    expect(
      within(transcript).getByText(/Playback cut by barge-in/),
    ).toBeInTheDocument()
  })

  it('refuses a branded price and shows the buyer handed to a human', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).getByText(/pricing there is on enquiry/)).toBeInTheDocument()
    expect(within(transcript).queryByText(/Bugatti.*AED/)).not.toBeInTheDocument()

    const ambassador = screen.getByLabelText('Ambassador view')
    expect(within(ambassador).getByText('Handed to a human')).toBeInTheDocument()
    expect(
      within(ambassador).getByText(/branded collection pricing enquiry/),
    ).toBeInTheDocument()
  })

  it('shows the shortlist with the figures that are in data/inventory.json', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const ambassador = screen.getByLabelText('Ambassador view')
    const skyrise = project('binghatti-skyrise')
    const circle = project('binghatti-circle')

    expect(within(ambassador).getByText(skyrise.name)).toBeInTheDocument()
    expect(
      within(ambassador).getByText(aed(skyrise.price_from_aed!)),
    ).toBeInTheDocument()
    expect(within(ambassador).getByText(circle.name)).toBeInTheDocument()
    expect(
      within(ambassador).getByText(aed(circle.price_from_aed!)),
    ).toBeInTheDocument()

    // The derived booking figure, computed from the plan rather than typed in.
    const booking = skyrise.payment_plan![0]
    const derived = Math.round(skyrise.price_from_aed! * (booking.pct / 100))
    expect(within(ambassador).getByText(aed(derived))).toBeInTheDocument()

    // R6: placeholder figures are visibly marked, never presented as fact.
    expect(
      within(ambassador).getAllByText(/Illustrative figures/).length,
    ).toBeGreaterThan(0)
  })

  it('reports the guardrail cost against the whole wait, endpointing included', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const meter = screen.getByLabelText('Latency')
    expect(
      within(meter).getByText(/Every sentence on this turn was inspected in/),
    ).toBeInTheDocument()
    expect(
      within(meter).getByText(/The model dominates the budget, not the safety layer/),
    ).toBeInTheDocument()
  })

  it('counts endpointing into the wait, and never adds transcription to it', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    const meter = screen.getByLabelText('Latency')

    // Turn 4's own numbers, from the fixture: endpointing 447.1, of which
    // transcription 300.8, and TTS first audio at 1011.5 from the turn clock.
    expect(within(meter).getByText('Endpointing')).toBeInTheDocument()
    expect(within(meter).getByText('of which, transcription')).toBeInTheDocument()
    expect(within(meter).getByText('of which, detector wait')).toBeInTheDocument()

    // Endpointing happens before the turn clock starts, so it is added back:
    // 447.1 + 1011.5 = 1,459 ms. It appears twice, as this turn's headline and
    // as the session p50, which is the same turn.
    expect(within(meter).getAllByText('1,459 ms').length).toBeGreaterThan(0)
    expect(
      within(meter).getByText(/Endpointing included: it happens before the turn clock/),
    ).toBeInTheDocument()

    // Transcription is a component of endpointing, taken from the same anchor.
    // Summing the two would report 1,759 ms, a stage that does not exist.
    expect(within(meter).queryByText('1,759 ms')).not.toBeInTheDocument()
  })

  it('names a pooled TTS socket on the turn after a barge-in', async () => {
    renderSurface()
    await startCall()
    await play(WHOLE_CALL)

    // Turn 4 follows turn 3's barge-in. `reused: true` here is issue #18's fix.
    expect(
      within(screen.getByLabelText('Latency')).getByText(
        /Audio came off a pooled socket/,
      ),
    ).toBeInTheDocument()
  })

  it('offers only the languages that have native-authored disclosure copy', () => {
    renderSurface()
    expect(screen.getByRole('button', { name: 'English' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Arabic' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Hindi' })).toBeDisabled()
    expect(
      screen.getByText(/neither has native-authored\s+disclosure copy/),
    ).toBeInTheDocument()
  })
})
