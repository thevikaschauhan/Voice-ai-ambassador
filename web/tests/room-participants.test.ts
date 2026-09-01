// @vitest-environment node
import { ParticipantKind } from 'livekit-client'
import { describe, expect, it } from 'vitest'
import { isAgent } from '@/lib/session/room-signals'

/**
 * Which side of the call a participant is on.
 *
 * Worth its own test because getting it wrong swaps two indicators on screen
 * and nothing else complains: the waveform still moves, the levels are still
 * right, and the room simply watches the wrong label light up. The live
 * verification covers the agent branch; these cover the rest, including the
 * buyer branch it cannot reach with one publisher.
 */
describe('telling the agent from the buyer', () => {
  it('reads the framework’s own participant kind first', () => {
    expect(isAgent({ kind: ParticipantKind.AGENT, identity: 'anything' })).toBe(true)
  })

  it('falls back to the identity prefix for a worker that predates the kind', () => {
    expect(isAgent({ kind: ParticipantKind.STANDARD, identity: 'agent-verify' })).toBe(true)
  })

  it('treats an ordinary participant as the buyer', () => {
    expect(isAgent({ kind: ParticipantKind.STANDARD, identity: 'buyer-1a2b' })).toBe(false)
    expect(isAgent({ kind: ParticipantKind.STANDARD, identity: 'sip_+971500000000' })).toBe(false)
  })

  it('does not mistake a name that merely contains "agent"', () => {
    // The prefix is anchored on purpose. A buyer identity is not ours to
    // constrain, and "management" or "agentina" must not read as the agent.
    expect(isAgent({ kind: ParticipantKind.STANDARD, identity: 'management' })).toBe(false)
    expect(isAgent({ kind: ParticipantKind.STANDARD, identity: 'my-agent-x' })).toBe(false)
  })
})
