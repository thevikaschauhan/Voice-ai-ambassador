import { TextMode } from '@/components/text-mode'
import { loadInventory } from '@/lib/inventory'

export const dynamic = 'force-dynamic'

export default async function TextPage() {
  const projects = await loadInventory()
  return <TextMode projects={projects} />
}
