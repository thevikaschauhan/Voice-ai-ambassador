import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DemoSurface } from '@/components/demo-surface'
import { LANGUAGES, PROJECTS } from './fixtures'

/**
 * The defence-in-depth demo, asserted on what the buyer hears in each of the
 * four positions of the toggle pair. The claim is not that a flag changed; it
 * is that the same question produces a different spoken answer.
 */

const FABRICATED = 'Bugatti Residences by Binghatti start from around twenty million dirhams for a two-bedroom.'
const WHOLE_CALL = 60_000

function press(name: string) {
  return act(async () => {
    screen.getByRole('button', { name }).click()
  })
}

async function runCall() {
  await press('Start call')
  await act(async () => {
    vi.advanceTimersByTime(WHOLE_CALL)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  render(<DemoSurface projects={PROJECTS} languages={LANGUAGES} live={false} />)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('the GUARDRAIL_MODE and PROMPT_MODE pair', () => {
  it('speaks a fabricated price when the prompt is generic and the guardrail only warns', async () => {
    await press('naive')
    await press('warn')
    await runCall()

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).getByText(FABRICATED)).toBeInTheDocument()
    expect(within(transcript).getAllByText('Recorded, spoken anyway')).toHaveLength(2)
    expect(
      within(transcript).getByText(/figure 20000000\.0 is not in the allowed set/),
    ).toBeInTheDocument()
  })

  it('stops the same sentence and hands the buyer over when the guardrail enforces', async () => {
    await press('naive')
    await runCall()

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).queryByText(FABRICATED)).not.toBeInTheDocument()
    expect(within(transcript).getAllByText('Blocked before synthesis')).toHaveLength(2)
    expect(
      within(transcript).getByText(
        'I do not want to quote you anything I cannot confirm. Let me put you through to one of our ambassadors.',
      ),
    ).toBeInTheDocument()
    expect(
      within(screen.getByLabelText('Ambassador view')).getByText('Handed to a human'),
    ).toBeInTheDocument()
  })

  it('labels the generic pairing honestly rather than by its flag values', async () => {
    await press('naive')
    await press('warn')
    expect(screen.getByText(/Typical chatbot configuration/)).toBeInTheDocument()
  })

  it('says why the ambassador prompt with warn only appears to do nothing', async () => {
    await press('warn')
    await runCall()

    expect(screen.getByText(/predicts this pairing underwhelms/)).toBeInTheDocument()
    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).queryByText(FABRICATED)).not.toBeInTheDocument()
    expect(within(transcript).getByText(/pricing is on enquiry rather than published/)).toBeInTheDocument()
  })

  it('restarts the call when a mode changes rather than mutating it mid-session', async () => {
    await runCall()
    expect(
      within(screen.getByLabelText('Transcript')).getByText(/My budget is around two million/),
    ).toBeInTheDocument()

    await press('naive')

    expect(
      within(screen.getByLabelText('Transcript')).getByText(/The call has not started/),
    ).toBeInTheDocument()
  })
})
