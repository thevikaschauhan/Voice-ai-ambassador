import { RoomUnavailable, liveKitConfig } from '@/lib/livekit/config'
import { mintViewerGrant } from '@/lib/livekit/room'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * Hands the browser a listen-only ticket to the call in progress.
 *
 * What crosses the wire is the signalling URL, the room name, and a token that
 * expires in ten minutes and can do exactly one thing: subscribe to audio in
 * one named room. The API secret that signed it stays here, which is the whole
 * reason this route exists rather than the browser talking to LiveKit itself.
 */
export async function GET(): Promise<Response> {
  if (liveKitConfig() === null) {
    // Not an error: a machine with no LiveKit configured is the ordinary state
    // for replay work, and the surface keeps its honest "no audio track" label.
    return Response.json({ room: null, reason: 'LiveKit is not configured' }, { status: 503 })
  }
  try {
    const grant = await mintViewerGrant()
    return Response.json(grant, {
      // A bearer token, however short-lived, is not something to leave in a
      // shared cache.
      headers: { 'cache-control': 'no-store' },
    })
  } catch (error) {
    // The reason is carried through rather than flattened. "cannot reach
    // LiveKit" sends an operator to check the network; a 401 from a reachable
    // host is a credential that does not match its key, and those are
    // different afternoons.
    return Response.json({ room: null, reason: describe(error) }, { status: 503 })
  }
}

function describe(error: unknown): string {
  if (error instanceof RoomUnavailable) return error.message
  if (error instanceof Error) {
    return /invalid token|unauthorized|401/i.test(error.message)
      ? 'LiveKit rejected the API key and secret for this project'
      : `LiveKit call failed: ${error.message}`
  }
  return 'LiveKit is unavailable'
}
