import { TextMode } from '@/components/text-mode'
import { loadInventory } from '@/lib/inventory'
import { agentDir } from '@/lib/textmode/process'

export const dynamic = 'force-dynamic'

export default async function TextPage() {
  const projects = await loadInventory()
  // Decided on the server for the same reason the call surface decides
  // liveness there: the page must know what it is showing before it renders,
  // not a frame later.
  return <TextMode projects={projects} live={agentDir() !== null} />
}
