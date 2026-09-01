import 'server-only'

import { randomUUID } from 'node:crypto'
import { AccessToken, RoomServiceClient } from 'livekit-server-sdk'
import { RoomUnavailable, liveKitConfig } from '@/lib/livekit/config'
import type { LiveKitConfig } from '@/lib/livekit/config'

/**
 * A token that lets the demo surface LISTEN to the call, and do nothing else.
 *
 * The grant is the security boundary here, so it is spelled out rather than
 * defaulted:
 *
 *   room          exactly one, by name. Not a wildcard - a token that can join
 *                 any room is the API secret with extra steps.
 *   canPublish    false. The surface is watching a call, not joining one. It
 *                 has no microphone (AGENTS.md: the UI makes no provider
 *                 calls) and must not be able to put audio in front of a buyer.
 *   canPublishData false. There is no command channel to the agent from here,
 *                 the same decision the events bridge made.
 *   hidden        true. The agent should not see a second participant appear
 *                 mid-call; a phantom in the room is a behaviour change, and
 *                 this is meant to be an observer.
 *   ttl           ten minutes. A token that leaks off the demo laptop stops
 *                 working before anyone could use it.
 */
const TOKEN_TTL = '10m'

export interface RoomGrant {
  url: string
  token: string
  room: string
}

/**
 * Which room the agent is in.
 *
 * `LIVEKIT_ROOM` pins it when someone wants it pinned. Otherwise the server
 * asks LiveKit, which is the answer that cannot go stale: the agent's room name
 * comes from its job dispatch, so a name configured on this side would be a
 * second source of truth for something the server already knows.
 *
 * A room with an agent in it wins over an empty one, because an empty room is
 * usually the residue of a finished call.
 */
async function activeRoom(config: LiveKitConfig): Promise<string> {
  const pinned = process.env.LIVEKIT_ROOM?.trim()
  if (pinned) return pinned

  const service = new RoomServiceClient(config.url, config.apiKey, config.apiSecret)
  const rooms = await service.listRooms()
  if (rooms.length === 0) {
    throw new RoomUnavailable('no LiveKit room is open; the agent is not in a call')
  }
  const occupied = rooms.filter((room) => room.numParticipants > 0)
  const chosen = (occupied.length > 0 ? occupied : rooms).sort(
    (a, b) => Number(b.creationTime) - Number(a.creationTime),
  )[0]
  return chosen.name
}

export async function mintViewerGrant(): Promise<RoomGrant> {
  const config = liveKitConfig()
  if (config === null) {
    throw new RoomUnavailable('LiveKit is not configured in this environment')
  }
  const room = await activeRoom(config)

  const token = new AccessToken(config.apiKey, config.apiSecret, {
    identity: `demo-surface-${randomUUID().slice(0, 8)}`,
    name: 'Demo surface',
    ttl: TOKEN_TTL,
  })
  token.addGrant({
    roomJoin: true,
    room,
    canPublish: false,
    canPublishData: false,
    canSubscribe: true,
    canUpdateOwnMetadata: false,
    hidden: true,
  })

  return { url: config.url, token: await token.toJwt(), room }
}
