import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TalkCall } from '@/components/talk-call'
import { TalkOrb } from '@/components/talk-orb'
import { TalkSubtitles } from '@/components/talk-subtitles'
import { AMBASSADOR_FALLBACK } from '@/lib/ambassador.shared'
import type { TalkEvents, TalkLine } from '@/lib/talk/session'

/**
 * The orb, its states, and the subtitles under it.
 *
 * The states are driven through the real component by handing it levels and
 * lines the way a room would, because "the orb reacts to the conversation" is a
 * claim about a derivation, and the derivation is the thing that can be wrong.
 * What a corona LOOKS like is not assertable here and is in the PR's
 * screenshots instead.
 */

let captured: TalkEvents | null = null

vi.mock('@/lib/talk/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/talk/session')>()
  return {
    ...actual,
    startTalking: vi.fn(async (_grant: unknown, events: TalkEvents) => {
      captured = events
      events.onPhase('live')
      return { end: async () => {}, setMuted: async () => {} }
    }),
  }
})

const NAMES = { en: 'Jane', ar: '', hi: '' } as const

function mintOk(): typeof fetch {
  return (async () =>
    ({
      ok: true,
      status: 200,
      json: async () => ({
        url: 'wss://example.livekit.cloud',
        token: 'a-token',
        room: 'demo-abc',
        identity: 'visitor-1234',
        language: 'en',
      }),
    }) as Response) as typeof fetch
}

async function startACall() {
  await userEvent.type(screen.getByLabelText('Access code'), 'the-code')
  await userEvent.click(screen.getByRole('button', { name: 'Start call' }))
  await waitFor(() => expect(captured).not.toBeNull())
}

function orbState(): string {
  const orb = document.querySelector('[data-orb-state]')
  return orb?.getAttribute('data-orb-state') ?? 'missing'
}

beforeEach(() => {
  captured = null
  vi.stubGlobal('fetch', mintOk())
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the orb follows the conversation', () => {
  it('is idle before a call and listening once one is up', async () => {
    render(<TalkCall names={NAMES} />)
    expect(orbState()).toBe('idle')
    await startACall()
    await waitFor(() => expect(orbState()).toBe('listening'))
  })

  it('blooms for the ambassador when her level rises', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    captured?.onLevels?.({ agent: 0.4, visitor: 0 })
    await waitFor(() => expect(orbState()).toBe('speaking'))
    expect(screen.getByText('Speaking')).toBeInTheDocument()
  })

  it('shows a distinct state for the visitor speaking', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    captured?.onLevels?.({ agent: 0, visitor: 0.3 })
    await waitFor(() => expect(orbState()).toBe('visitor'))
    expect(screen.getByText('Hearing you')).toBeInTheDocument()
  })

  it('lets the ambassador win when both sides are audible', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    // An open microphone picks her up through the speakers, so the corona has
    // to follow whoever is actually talking rather than flickering between them.
    captured?.onLevels?.({ agent: 0.5, visitor: 0.4 })
    await waitFor(() => expect(orbState()).toBe('speaking'))
  })

  it('shows THINKING after the visitor finishes and before she answers', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    captured?.onLine({ id: 'SG_1', speaker: 'visitor', text: 'what does it cost', final: true })
    captured?.onLevels?.({ agent: 0, visitor: 0 })
    // The one moment a silent page reads as a broken demo.
    await waitFor(() => expect(orbState()).toBe('thinking'))
    expect(screen.getByText('Thinking')).toBeInTheDocument()
  })

  it('leaves thinking as soon as she starts', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    captured?.onLine({ id: 'SG_1', speaker: 'visitor', text: 'what does it cost', final: true })
    captured?.onLevels?.({ agent: 0, visitor: 0 })
    await waitFor(() => expect(orbState()).toBe('thinking'))
    captured?.onLevels?.({ agent: 0.35, visitor: 0 })
    await waitFor(() => expect(orbState()).toBe('speaking'))
  })

  it('does not treat an unfinished visitor line as a thinking pause', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    // Still mid-sentence: interim text is not a finished turn.
    captured?.onLine({ id: 'SG_1', speaker: 'visitor', text: 'what does', final: false })
    captured?.onLevels?.({ agent: 0, visitor: 0 })
    await waitFor(() => expect(orbState()).toBe('listening'))
  })

  it('returns to idle when the call ends', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    captured?.onPhase('ended')
    captured?.onEnded({ kind: 'ended', message: 'The ambassador ended the call.' })
    await waitFor(() => expect(orbState()).toBe('idle'))
  })
})

describe('the ambassador has a name, and it is used', () => {
  it('labels the orb with the name for the call’s language', async () => {
    render(<TalkCall names={NAMES} />)
    await startACall()
    expect(screen.getAllByText('Jane').length).toBeGreaterThan(0)
  })

  it('prefixes her subtitle lines with it, and the visitor’s with You', () => {
    const lines: TalkLine[] = [
      { id: 'SG_1', speaker: 'visitor', text: 'what does it cost', final: true },
      { id: 'SG_2', speaker: 'agent', text: 'From two million dirhams.', final: true },
    ]
    render(<TalkSubtitles lines={lines} name="Jane" idle="nothing yet" />)
    expect(screen.getByText('Jane')).toBeInTheDocument()
    expect(screen.getByText('You')).toBeInTheDocument()
  })

  it('falls back rather than showing a blank label for an unnamed language', () => {
    render(<TalkOrb state="listening" level={0} name={AMBASSADOR_FALLBACK} />)
    expect(screen.getByText(AMBASSADOR_FALLBACK)).toBeInTheDocument()
  })
})

describe('the subtitles', () => {
  const lines: TalkLine[] = [
    { id: 'SG_1', speaker: 'visitor', text: 'first thing', final: true },
    { id: 'SG_2', speaker: 'agent', text: 'second thing', final: true },
    { id: 'SG_3', speaker: 'visitor', text: 'third thing', final: true },
    { id: 'SG_4', speaker: 'agent', text: 'the line being spoken now', final: false },
  ]

  it('shows the current line and the one before it, and folds the rest away', () => {
    render(<TalkSubtitles lines={lines} name="Jane" idle="nothing yet" />)
    expect(screen.getByText('the line being spoken now')).toBeInTheDocument()
    expect(screen.getByText('third thing')).toBeInTheDocument()
    // Earlier lines are kept but not shouted: a visitor mid-sentence should not
    // be handed a wall of text.
    const history = screen.getByText(/Earlier in this call/)
    expect(history).toBeInTheDocument()
    expect(within(history.closest('details') as HTMLElement).getByText('first thing')).toBeInTheDocument()
  })

  it('says something honest when nothing has been transcribed', () => {
    render(<TalkSubtitles lines={[]} name="Jane" idle="Say hello whenever you are ready." />)
    expect(screen.getByText('Say hello whenever you are ready.')).toBeInTheDocument()
  })
})

describe('prefers-reduced-motion', () => {
  it('keeps the corona and drops the movement, with the state still in words', () => {
    vi.stubGlobal('matchMedia', ((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia)
    render(<TalkOrb state="speaking" level={0.8} name="Jane" />)
    const orb = document.querySelector('[data-orb-state]') as HTMLElement
    // No animation classes at all, so there is nothing for the keyframes to
    // drive; the state is carried by the label instead.
    expect(orb.querySelectorAll('.orb-spin, .orb-spin-slow, .orb-drift, .orb-drift-slow, .orb-breathe').length).toBe(0)
    expect(screen.getByText('Speaking')).toBeInTheDocument()
  })

  it('animates when motion is fine', () => {
    render(<TalkOrb state="listening" level={0} name="Jane" />)
    const orb = document.querySelector('[data-orb-state]') as HTMLElement
    expect(orb.querySelectorAll('.orb-spin, .orb-spin-slow').length).toBeGreaterThan(0)
  })
})

describe('the picker offers only what the deployment offers', () => {
  it('shows a picker when there is a choice to make', () => {
    render(<TalkCall names={NAMES} offered={['en', 'ar', 'hi']} />)
    const picker = screen.getByLabelText('Language') as HTMLSelectElement
    expect(picker.tagName).toBe('SELECT')
    expect([...picker.options].map((option) => option.value)).toEqual(['en', 'ar', 'hi'])
  })

  it('renders the offered subset and nothing else', () => {
    render(<TalkCall names={NAMES} offered={['en', 'hi']} />)
    const picker = screen.getByLabelText('Language') as HTMLSelectElement
    expect([...picker.options].map((option) => option.value)).toEqual(['en', 'hi'])
  })

  it('drops the picker entirely for a single language, and still says which', () => {
    render(<TalkCall names={NAMES} offered={['en']} />)
    // One option is not a choice: a select asking a visitor to decide something
    // already decided is worse than a label.
    expect(screen.queryByLabelText('Language')).not.toBeInTheDocument()
    expect(screen.getByText('Language')).toBeInTheDocument()
    expect(screen.getByText('English')).toBeInTheDocument()
  })

  it('opens on the first offered language rather than a hardcoded English', () => {
    render(<TalkCall names={NAMES} offered={['hi']} />)
    // A deployment offering Hindi only must not open with a selection its own
    // picker cannot show.
    expect(screen.getByText('हिन्दी')).toBeInTheDocument()
  })

  it('sends the offered language to the route', async () => {
    const sent: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (_input: RequestInfo | URL, init?: RequestInit) => {
        sent.push(String(JSON.parse(String(init?.body)).language))
        return {
          ok: true,
          status: 200,
          json: async () => ({
            url: 'wss://x',
            token: 't',
            room: 'demo-1',
            identity: 'visitor-1',
            language: 'hi',
          }),
        } as Response
      }) as typeof fetch,
    )
    render(<TalkCall names={NAMES} offered={['hi']} />)
    await userEvent.type(screen.getByLabelText('Access code'), 'the-code')
    await userEvent.click(screen.getByRole('button', { name: 'Start call' }))
    await waitFor(() => expect(sent).toEqual(['hi']))
  })
})
