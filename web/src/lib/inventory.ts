import 'server-only'

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { Project } from '@/lib/types'

/**
 * The demo surface holds no inventory of its own.
 *
 * AGENTS.md invariant 1: project names, prices, sizes, handover dates and
 * payment structures come from `data/inventory.json` only. The shortlist the
 * agent produces is a list of ids; this loader is how those ids become figures
 * on screen, so a price cannot drift between the agent and the UI - there is
 * one copy of it and the UI reads that one.
 *
 * Server side only, and deliberately not cached in module scope: the demo
 * machine edits this file between runs and a stale price on stage would be
 * exactly the class of error the whole system exists to prevent.
 */
export async function loadInventory(): Promise<Project[]> {
  const path = join(process.cwd(), '..', 'data', 'inventory.json')
  const raw = await readFile(path, 'utf-8')
  const parsed: unknown = JSON.parse(raw)
  if (!Array.isArray(parsed)) {
    throw new Error(`data/inventory.json is not an array (got ${typeof parsed})`)
  }
  return parsed as Project[]
}

export { milestoneAmounts, resolveShortlist } from '@/lib/inventory.shared'
