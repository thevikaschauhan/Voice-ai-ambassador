import 'server-only'

/**
 * The LiveKit credentials, read once, server side.
 *
 * `LIVEKIT_API_SECRET` signs tokens. It never reaches the browser, is never
 * logged, and is never returned by any route: what the browser gets is a
 * short-lived token minted from it with the narrowest grant that lets it
 * listen (`lib/livekit/room.ts`). AGENTS.md's hard rule is that provider
 * credentials live in server-side env only, and a signing secret is the
 * clearest case of it - anyone holding this can mint a token for any room.
 */

export interface LiveKitConfig {
  url: string
  apiKey: string
  apiSecret: string
}

export class RoomUnavailable extends Error {}

export function liveKitConfig(): LiveKitConfig | null {
  const url = process.env.LIVEKIT_URL?.trim()
  const apiKey = process.env.LIVEKIT_API_KEY?.trim()
  const apiSecret = process.env.LIVEKIT_API_SECRET?.trim()
  if (!url || !apiKey || !apiSecret) return null
  return { url, apiKey, apiSecret }
}

/** The signalling URL is not a secret; the browser has to dial it. */
export function publicUrl(config: LiveKitConfig): string {
  return config.url
}
