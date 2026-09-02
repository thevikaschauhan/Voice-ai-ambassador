import { TextMode } from '@/components/text-mode'
import { loadInventory } from '@/lib/inventory'
import { textModeAvailability } from '@/lib/textmode/availability'

export const dynamic = 'force-dynamic'

export default async function TextPage() {
  const projects = await loadInventory()
  // Decided on the server for the same reason the call surface decides
  // liveness there: the page must know what it is showing before it renders,
  // not a frame later. Three states now rather than two - the hosted service
  // refuses instead of replaying (docs/09-).
  return <TextMode projects={projects} availability={textModeAvailability()} />
}
