import { RoomUnavailable, liveKitConfig } from '@/lib/livekit/config'
import { DemoAtCapacity, isTalkLanguage, mintTalkGrant } from '@/lib/livekit/talk'
import { checkAccessCode } from '@/lib/hosted'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * Starts a call: one fresh room, one publish-capable token, one visitor.
 *
 * POST rather than GET, because it CREATES a room. A GET that mints a metered
 * resource is one prefetch away from a bill.
 *
 * The order of the checks is the security order, not the convenient one: the
 * access code is verified before anything reaches LiveKit, so an attempt with
 * the wrong code costs this service one string comparison rather than a
 * `listRooms` round trip. Everything a refused visitor learns is that they were
 * refused.
 */
interface Body {
  code?: unknown
  language?: unknown
}

export async function POST(request: Request): Promise<Response> {
  let body: Body
  try {
    body = (await request.json()) as Body
  } catch {
    return refuse('body must be JSON', 400)
  }

  const gate = checkAccessCode(body.code)
  if (gate !== 'ok') {
    // Deliberately the same answer either way. A visitor who learns that the
    // service has no code configured has learned something useful about a
    // service they cannot use; the distinction is in the server log, where an
    // operator can act on it.
    console.warn(
      gate === 'closed'
        ? 'talk: refused because DEMO_ACCESS_CODE is not set; an unset gate is a closed gate'
        : 'talk: refused an attempt with the wrong access code',
    )
    return refuse('that access code was not accepted', 403)
  }

  if (!isTalkLanguage(body.language)) {
    return refuse('pick one of the offered languages', 400)
  }

  if (liveKitConfig() === null) {
    return refuse('this demo is not connected to LiveKit', 503)
  }

  try {
    const grant = await mintTalkGrant(body.language)
    return Response.json(grant, {
      // A publish-capable bearer token. Nothing caches this.
      headers: { 'cache-control': 'no-store' },
    })
  } catch (error) {
    if (error instanceof DemoAtCapacity) {
      // 429 rather than 503: the service is fine, the visitor should come back.
      return refuse(error.message, 429)
    }
    if (error instanceof RoomUnavailable) {
      return refuse(error.message, 503)
    }
    // The reason is carried through for the operator the same way the viewer
    // route carries it: "cannot reach LiveKit" and "LiveKit rejected the key"
    // are different afternoons.
    const detail = error instanceof Error ? error.message : 'unknown error'
    console.error(`talk: could not start a call: ${detail}`)
    return refuse(
      /invalid|unauthorized|401/i.test(detail)
        ? 'LiveKit rejected this project key and secret'
        : 'could not start a call just now',
      502,
    )
  }
}

function refuse(reason: string, status: number): Response {
  return Response.json({ room: null, reason }, { status, headers: { 'cache-control': 'no-store' } })
}
