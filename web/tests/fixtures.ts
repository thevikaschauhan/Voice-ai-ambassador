import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { LANGUAGES as AMBASSADOR_LANGUAGES } from '@/lib/ambassador'
import type { AmbassadorNames } from '@/lib/ambassador.shared'
import { AMBASSADOR_FALLBACK } from '@/lib/ambassador.shared'
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

/**
 * A complete `AmbassadorNames` from the one or two entries a test cares about.
 *
 * `loadAmbassadorNames` always returns every language, so the type is total and
 * a fixture has to be total too. Built from the loader's own language list so
 * that widening `Language` does not mean hand-editing every call site - which
 * is what pushed the type to `Partial` and hid the totality in the first place.
 */
export function ambassadorNames(overrides: Partial<AmbassadorNames> = {}): AmbassadorNames {
  const base = Object.fromEntries(
    AMBASSADOR_LANGUAGES.map((language) => [language, AMBASSADOR_FALLBACK]),
  ) as AmbassadorNames
  return { ...base, ...overrides }
}
