import 'server-only'

import { randomUUID } from 'node:crypto'
import { AccessToken, RoomServiceClient } from 'livekit-server-sdk'
import { RoomUnavailable, liveKitConfig } from '@/lib/livekit/config'
import type { LiveKitConfig } from '@/lib/livekit/config'
import { maxRooms, offeredLanguages } from '@/lib/hosted'
import { TALK_LANGUAGES, isTalkLanguage } from '@/lib/livekit/talk.shared'
import type { TalkLanguage } from '@/lib/livekit/talk.shared'

/**
 * A token that lets one visitor TALK to the ambassador, and a fresh room to do
 * it in.
 *
 * This sits beside `mintViewerGrant` rather than inside it, and the separation
 * is the point (docs/09-): the viewer grant deliberately withholds exactly the
 * capability this one needs, so the two intentions cannot be confused in
 * review. A single function with a `canPublish` argument is one wrong call away
 * from handing a publish token to the read-only surface.
 *
 * The grant, spelled out because it is the security boundary:
 *
 *   room           exactly one, freshly created here, by name. The visitor
 *                  cannot be handed a token for somebody else's conversation
 *                  because the room did not exist until this call.
 *   canPublish     TRUE, and only here. The microphone goes to LiveKit
 *                  transport; recognition, synthesis and inference all stay in
 *                  the worker with the keys, so this moves no credential and
 *                  does not breach the rule that the UI makes no provider
 *                  calls (docs/09-).
 *   canPublishData false. There is still no command channel to the agent from
 *                  a browser. The transcript flows the other way, and audio is
 *                  the only thing a visitor sends.
 *   hidden         FALSE. A hidden participant is not a participant: the
 *                  worker has to see the visitor arrive or there is no call.
 *                  The viewer grant hides itself for the opposite reason.
 *   ttl            fifteen minutes, a demo conversation with headroom. Long
 *                  enough that a visitor is not cut off mid-sentence, short
 *                  enough that a token copied out of the page expires before
 *                  it is worth anything.
 */

/** Long enough for a real conversation, short enough to be worthless if copied. */
const TOKEN_TTL = '15m'

/**
 * Seconds to hold a room open before anyone joins.
 *
 * The visitor's browser connects within a second or two of this call. The
 * window only has to cover a slow handshake, and a room left open by a visitor
 * who never connected counts against the concurrency cap until it closes.
 */
const EMPTY_TIMEOUT_S = 60

/**
 * Seconds to hold a room open after the last participant leaves.
 *
 * This is the one that reclaims a room when the client closes the tab, which
 * is how a demo actually ends. Without it the cap counts rooms nobody is in
 * (docs/09-). Short, with just enough grace for a page reload.
 */
const DEPARTURE_TIMEOUT_S = 20

/** The visitor and the agent. Nobody else belongs in a demo room. */
const MAX_PARTICIPANTS = 2

/**
 * The cross-service contract, version 1.
 *
 * A STRING, because `createRoom`'s `metadata` is a string - the language has to
 * be serialised, and the worker parses this exact shape (`task-hosted-
 * language-from-metadata`). The `v` is here so that a later shape can be added
 * without the worker guessing which one it is holding, and the worker falls
 * back to its `LANGUAGE` env value when the metadata is absent or unparseable,
 * so an older worker meeting a newer room degrades instead of failing.
 */
export function roomMetadata(language: TalkLanguage): string {
  return JSON.stringify({ v: 1, language })
}

export class DemoAtCapacity extends Error {}

export interface TalkGrant {
  url: string
  token: string
  room: string
  language: TalkLanguage
  identity: string
}

/**
 * Rooms currently occupying the cap.
 *
 * Counted from LiveKit rather than kept here, because this process is not the
 * only thing that could have created one and a counter in module scope would
 * be wrong after a restart. This is the call `activeRoom` already makes, reused
 * (docs/09-).
 */
async function demoRoomCount(service: RoomServiceClient): Promise<number> {
  const rooms = await service.listRooms()
  return rooms.filter((room) => room.name.startsWith(ROOM_PREFIX)).length
}

/**
 * The prefix that marks a room as one of ours.
 *
 * The cap counts only these. A room created by some other tool in the same
 * LiveKit project should not lock the demo out, and - more importantly - the
 * viewer path uses this prefix to tell a demo room from the laptop's own.
 */
export const ROOM_PREFIX = 'demo-'

export async function mintTalkGrant(language: TalkLanguage): Promise<TalkGrant> {
  const config = liveKitConfig()
  if (config === null) {
    throw new RoomUnavailable('LiveKit is not configured in this environment')
  }
  const service = new RoomServiceClient(config.url, config.apiKey, config.apiSecret)

  // Counted BEFORE the room is created, so the cap is a cap and not a
  // suggestion. Two visitors arriving in the same instant can both pass this
  // check; that is a known and accepted race - the cost is one extra room, and
  // the alternative is a lock this service has nowhere to keep.
  const cap = maxRooms()
  if ((await demoRoomCount(service)) >= cap) {
    throw new DemoAtCapacity(
      `the demo is at capacity: ${cap} ${cap === 1 ? 'call' : 'calls'} already in progress`,
    )
  }

  const room = `${ROOM_PREFIX}${randomUUID()}`
  await service.createRoom({
    name: room,
    metadata: roomMetadata(language),
    emptyTimeout: EMPTY_TIMEOUT_S,
    departureTimeout: DEPARTURE_TIMEOUT_S,
    maxParticipants: MAX_PARTICIPANTS,
  })

  const identity = `visitor-${randomUUID().slice(0, 8)}`
  const token = new AccessToken(config.apiKey, config.apiSecret, {
    identity,
    name: 'Guest',
    ttl: TOKEN_TTL,
  })
  token.addGrant({
    roomJoin: true,
    room,
    canPublish: true,
    canPublishData: false,
    canSubscribe: true,
    canUpdateOwnMetadata: false,
    hidden: false,
  })

  return { url: config.url, token: await token.toJwt(), room, language, identity }
}

export type { LiveKitConfig }
export { EMPTY_TIMEOUT_S, DEPARTURE_TIMEOUT_S, MAX_PARTICIPANTS, TOKEN_TTL }
export { TALK_LANGUAGES, isTalkLanguage, offeredLanguages }
export type { TalkLanguage }

/**
 * Is this language one THIS deployment offers?
 *
 * Narrower than `isTalkLanguage`, and the route checks this one: a code can be
 * a real language and still not be on offer here, which is what
 * `DEMO_LANGUAGES` exists to say. Checked server-side so the restriction holds
 * whatever the page renders - a picker is a convenience, not a gate.
 */
export function isOfferedLanguage(value: unknown): value is TalkLanguage {
  return isTalkLanguage(value) && offeredLanguages().includes(value)
}
