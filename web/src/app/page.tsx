import { DemoSurface } from '@/components/demo-surface'
import { BridgeUnavailable, handshakePath, readHandshake } from '@/lib/bridge/handshake'
import { loadInventory } from '@/lib/inventory'
import { loadLanguageReadiness } from '@/lib/readiness'

export const dynamic = 'force-dynamic'

/**
 * Inventory, language readiness and liveness are all decided on the server.
 *
 * The client never holds a price of its own: the agent's shortlist is a list of
 * ids and this is where those ids become figures, so there is exactly one copy
 * of every price and it is `data/inventory.json` (AGENTS.md invariant 1).
 *
 * Liveness is settled here rather than by a probe from the browser, so the
 * surface knows what it is showing before it renders anything - a page that
 * says "replay" for one frame and then flips to "live" is a page nobody can
 * trust at a glance. What crosses to the client is a boolean and a reason:
 * never the port, never the token.
 */
export default async function Page() {
  const [projects, languages, live] = await Promise.all([
    loadInventory(),
    loadLanguageReadiness(),
    probeLive(),
  ])
  return (
    <DemoSurface
      projects={projects}
      languages={languages}
      live={live.live}
      liveReason={live.reason}
    />
  )
}

async function probeLive(): Promise<{ live: boolean; reason?: string }> {
  if (handshakePath() === null) {
    return { live: false, reason: 'No agent handshake configured.' }
  }
  try {
    await readHandshake()
    return { live: true }
  } catch (error) {
    return {
      live: false,
      reason:
        error instanceof BridgeUnavailable
          ? `${error.message}.`
          : 'The agent handshake could not be read.',
    }
  }
}
