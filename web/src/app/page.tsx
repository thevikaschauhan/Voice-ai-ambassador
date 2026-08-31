import { DemoSurface } from '@/components/demo-surface'
import { loadInventory } from '@/lib/inventory'
import { loadLanguageReadiness } from '@/lib/readiness'

export const dynamic = 'force-dynamic'

/**
 * Inventory and language readiness are read on the server, per request.
 *
 * The client never holds a price of its own: the agent's shortlist is a list
 * of ids and this is where those ids become figures, so there is exactly one
 * copy of every price and it is `data/inventory.json` (AGENTS.md invariant 1).
 */
export default async function Page() {
  const [projects, languages] = await Promise.all([
    loadInventory(),
    loadLanguageReadiness(),
  ])
  return <DemoSurface projects={projects} languages={languages} />
}
