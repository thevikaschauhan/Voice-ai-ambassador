import 'server-only'

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { AMBASSADOR_FALLBACK } from '@/lib/ambassador.shared'
import type { AmbassadorNames } from '@/lib/ambassador.shared'
import type { Language } from '@/lib/types'

/**
 * What the ambassador is called, per language.
 *
 * The client talks to somebody, not to a product, and a named ambassador is
 * what makes the surface read that way. The human chose Jane for English.
 *
 * Read at request time from the repo root, the same way the inventory is, so a
 * name changed on the demo machine between runs is a name changed on the next
 * page load - and so there is one copy of it rather than one here and one in
 * the agent's prompt.
 */

const LANGUAGES: readonly Language[] = ['en', 'ar', 'hi']

/**
 * A deliberately small reader rather than a YAML dependency, matching
 * `readiness.ts`: this file is three keys and their values. If it ever grows
 * structure, take the dependency instead of growing the parser.
 */
export async function loadAmbassadorNames(): Promise<AmbassadorNames> {
  let text = ''
  try {
    text = await readFile(join(process.cwd(), '..', 'data', 'ambassadors.yaml'), 'utf-8')
  } catch {
    // A missing file is the same answer as an empty one: every language falls
    // back. The page still opens, which matters more than the name.
    text = ''
  }
  return {
    en: nameFor(text, 'en'),
    ar: nameFor(text, 'ar'),
    hi: nameFor(text, 'hi'),
  }
}

function nameFor(text: string, language: Language): string {
  const line = text
    .split('\n')
    .find((candidate) => new RegExp(`^${language}:`).test(candidate.trim()))
  if (line === undefined) return AMBASSADOR_FALLBACK
  const raw = line.trim().slice(language.length + 1).trim()
  // Quoted empties are the "not authored yet" marker, the same convention the
  // other per-language tables use.
  const unquoted = raw.replace(/^["'](.*)["']$/, '$1').trim()
  return unquoted === '' ? AMBASSADOR_FALLBACK : unquoted
}

export { LANGUAGES, AMBASSADOR_FALLBACK }
export type { AmbassadorNames }
