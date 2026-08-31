import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { LanguageReadiness } from '@/lib/readiness'
import type { Project } from '@/lib/types'

/**
 * The tests read the real inventory file rather than a copy of it.
 *
 * A fixture copy of a price is a second copy of a price, which is the exact
 * thing AGENTS.md invariant 1 forbids: if `data/inventory.json` changes and
 * these tests keep passing against a stale figure, the tests are lying about
 * what the surface will show on stage.
 */
export const PROJECTS: Project[] = JSON.parse(
  readFileSync(join(process.cwd(), '..', 'data', 'inventory.json'), 'utf-8'),
) as Project[]

/** Matches the repository today: only English has native-authored disclosure copy. */
export const LANGUAGES: LanguageReadiness[] = [
  { language: 'en', ready: true },
  { language: 'ar', ready: false },
  { language: 'hi', ready: false },
]

export function project(id: string): Project {
  const found = PROJECTS.find((p) => p.id === id)
  if (found === undefined) throw new Error(`${id} is not in data/inventory.json`)
  return found
}
