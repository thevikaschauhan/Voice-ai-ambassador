import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DemoSurface } from '@/components/demo-surface'
import { LANGUAGES, PROJECTS, project } from './fixtures'
import { aed } from '@/lib/format'

/**
 * The live path, asserted on what the room sees.
 *
 * The events below are the shapes `agent/src/adapter/events.py` actually emits,
 * at full fidelity - which is the whole reason the bridge exists. They are fed
 * through a stub EventSource standing in for the network, so everything from
 * `liveSource` down to the rendered panels is the real code.
 */

class StubEventSource {
  static instances: StubEventSource[] = []
  readonly listeners = new Map<string, ((event: Event) => void)[]>()
  closed = false

  constructor(readonly url: string) {
    StubEventSource.instances.push(this)
  }

  addEventListener(type: string, handler: (event: Event) => void): void {
    const existing = this.listeners.get(type) ?? []
    existing.push(handler)
    this.listeners.set(type, existing)
  }

  close(): void {
    this.closed = true
  }

  /** What the route handler sends: `event: agent` with one JSON line. */
  emit(payload: object): void {
    this.dispatch('agent', new MessageEvent('agent', { data: JSON.stringify(payload) }))
  }

  dispatch(type: string, event: Event): void {
    for (const handler of this.listeners.get(type) ?? []) handler(event)
  }

  static current(): StubEventSource {
    const latest = StubEventSource.instances.at(-1)
    if (latest === undefined) throw new Error('nothing opened an EventSource')
    return latest
  }
}

function renderLive(room = false) {
  render(<DemoSurface projects={PROJECTS} languages={LANGUAGES} live room={room} />)
}

async function attach() {
  await act(async () => {
    screen.getByRole('button', { name: 'Attach to agent' }).click()
  })
  await act(async () => {
    StubEventSource.current().dispatch('open', new Event('open'))
  })
}

async function send(...payloads: object[]) {
  await act(async () => {
    for (const payload of payloads) StubEventSource.current().emit(payload)
  })
}

beforeEach(() => {
  StubEventSource.instances = []
  vi.stubGlobal('EventSource', StubEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('attached to a running agent', () => {
  it('says on screen that it is live rather than a fixture', () => {
    renderLive()
    expect(screen.getByText('Live agent')).toBeInTheDocument()
    expect(
      screen.getByText(/Attached to a running agent/),
    ).toBeInTheDocument()
    expect(screen.queryByText('Replay')).not.toBeInTheDocument()
  })

  it('renders the buyer’s own words, which the emitted stream does not carry', async () => {
    renderLive()
    await attach()
    await send({
      event: 'user_turn',
      turn: 1,
      text: 'I am looking to invest in Dubai. My budget is around two million.',
    })

    expect(
      within(screen.getByLabelText('Transcript')).getByText(
        /My budget is around two million/,
      ),
    ).toBeInTheDocument()
  })

  it('shows a blocked sentence, its validator and the figure it objected to', async () => {
    renderLive()
    await attach()
    await send(
      { event: 'user_turn', turn: 1, text: 'What does the Skyrise start at?' },
      {
        event: 'guardrail',
        turn: 1,
        outcome: 'blocked',
        mode: 'enforce',
        ms: 0.34,
        sentence_index: 0,
        raw: 'Binghatti Skyrise in Business Bay starts from AED 950,000.',
        spoken: null,
        validator: 'numeric_claims',
        detail: 'figure 950000.0 is not in the allowed set for this call',
        figures: [{ surface: 'AED 950,000', value: 950000, kind: 'amount' }],
      },
    )

    const transcript = screen.getByLabelText('Transcript')
    expect(within(transcript).getByText('Blocked before synthesis')).toBeInTheDocument()
    expect(
      within(transcript).getByText(/figure 950000\.0 is not in the allowed set/),
    ).toBeInTheDocument()
  })

  it('resolves a live brief’s shortlist against data/inventory.json', async () => {
    renderLive()
    await attach()
    await send({
      event: 'brief',
      turn: 1,
      brief: {
        intent: 'invest',
        budget: { amount: 2000000, currency: 'AED', confirmed: true },
        unit_preference: null,
        timeline: null,
        buyer_location: 'London',
        golden_visa_interest: null,
        hesitations: [],
        shortlist_ids: ['binghatti-skyrise'],
        stage: 'recommendation',
        language: 'en',
      },
    })

    const ambassador = screen.getByLabelText('Ambassador view')
    const skyrise = project('binghatti-skyrise')
    expect(within(ambassador).getByText(skyrise.name)).toBeInTheDocument()
    // The price came from the file, not from the event: the agent sent an id.
    expect(
      within(ambassador).getByText(aed(skyrise.price_from_aed!)),
    ).toBeInTheDocument()
  })

  it('counts a live turn’s endpointing into the wait', async () => {
    renderLive()
    await attach()
    await send(
      { event: 'user_turn', turn: 1, text: 'What would I pay upfront?' },
      {
        event: 'endpointing',
        turn: 1,
        endpoint_ms: 447.1,
        stt_ms: 300.8,
        after_transcript_ms: 146.3,
        turn_committed_ms: 453.3,
      },
      {
        event: 'turn_complete',
        turn: 1,
        endpoint_ms: 447.1,
        stt_ms: 300.8,
        llm_ttft_ms: 703.8,
        llm_first_sentence_ms: 946.1,
        guardrail_ms: 0.46,
        tts_first_audio_ms: 1011.5,
        total_ms: 1594.2,
        sentences: 2,
        violations: 0,
        regenerated: false,
        actions: [],
        reasoning_tokens: 0,
        audit_incomplete: false,
      },
    )

    const meter = screen.getByLabelText('Latency')
    // 447.1 + 1011.5 = 1,459 ms, and transcription is never added to it.
    expect(within(meter).getAllByText('1,459 ms').length).toBeGreaterThan(0)
    expect(within(meter).queryByText('1,759 ms')).not.toBeInTheDocument()
  })

  it('tolerates an event type it has never seen', async () => {
    renderLive()
    await attach()
    // docs/02-: consumers must tolerate new types. #21 added three after this
    // surface was written, so this is not hypothetical.
    await send(
      { event: 'tts_pool_reprewarm', turn: 1, outcome: 'reprewarmed' },
      { event: 'something_added_next_week', turn: 1, whatever: true },
      { event: 'user_turn', turn: 1, text: 'Still here.' },
    )

    expect(
      within(screen.getByLabelText('Transcript')).getByText('Still here.'),
    ).toBeInTheDocument()
  })

  it('says there is no audio track rather than drawing silence', async () => {
    renderLive()
    await attach()
    // The event stream carries turns, not samples. A flat trace would read as
    // a silent microphone, which is a different and wrong claim.
    expect(
      screen.getByText(/No audio track attached/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/These three read the agent’s turn events, not the microphone/),
    ).toBeInTheDocument()
  })

  it('reads a dropped link as lost, not as the call ending', async () => {
    renderLive()
    await attach()
    await act(async () => {
      StubEventSource.current().dispatch('error', new Event('error'))
    })

    // EventSource reconnects on its own, so this is "the link dropped" and the
    // designed banner says so - it is not a finished call.
    // Named in two places on purpose: the call panel's badge and the designed
    // banner that says what it means for the call.
    expect(screen.getAllByText('Connection lost')).toHaveLength(2)
    expect(screen.getByText(/The room has dropped/)).toBeInTheDocument()
  })

  it('reports the agent’s pairing instead of pretending to set it', async () => {
    renderLive()
    await attach()
    await send({
      event: 'session_start',
      model: 'qwen/qwen3.7-flash',
      language: 'en',
      prompt_mode: 'naive',
      guardrail_mode: 'warn',
      inventory_version: 'inventory.json@VERIFY-placeholder',
    })

    // Both modes are read once at session start by the agent process, so
    // nothing here can change a call already running. The controls say so
    // rather than looking live and doing nothing.
    expect(screen.getByRole('button', { name: 'ambassador' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'naive' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'naive' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'warn' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByText(/Reported by the running agent, not set from here/)).toBeInTheDocument()

    // And the honest label for the pairing the agent is actually running.
    expect(screen.getByText(/Typical chatbot configuration/)).toBeInTheDocument()
  })

  it('does not reconnect when a disabled toggle is clicked', async () => {
    renderLive()
    await attach()
    expect(StubEventSource.instances).toHaveLength(1)

    await act(async () => {
      screen.getByRole('button', { name: 'naive' }).click()
    })

    expect(StubEventSource.instances).toHaveLength(1)
    expect(StubEventSource.current().closed).toBe(false)
  })

  it('keeps the honest placeholder when the room cannot be joined', async () => {
    // The state this was written against: LiveKit is configured, so the surface
    // asks for a ticket, and the answer is 503. A room client that turned a
    // missing room into a broken page would make the demo more fragile than the
    // placeholder it replaced.
    vi.stubGlobal(
      'fetch',
      (async () =>
        new Response(JSON.stringify({ room: null, reason: 'no room' }), {
          status: 503,
        })) as unknown as typeof fetch,
    )
    renderLive(true)
    await attach()
    await send({
      event: 'user_turn',
      turn: 1,
      text: 'Still gets the transcript.',
    })

    expect(
      within(screen.getByLabelText('Transcript')).getByText('Still gets the transcript.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/No audio track attached/)).toBeInTheDocument()
    expect(
      screen.queryByText(/Measured from the call’s own audio/),
    ).not.toBeInTheDocument()
  })
})
