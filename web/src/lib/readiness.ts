import 'server-only'

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { Language } from '@/lib/types'

export interface LanguageReadiness {
  language: Language
  /** A language with no disclosure copy cannot open a call (docs/04-). */
  ready: boolean
}

/**
 * The languages a call can be OPENED in.
 *
 * Deliberately not every member of `Language`. The runtime gained seven more
 * codes as mid-call SWITCH TARGETS (docs/04-): the agent may move into one
 * after a call has started, but no call has ever begun in one, so offering
 * them here would put buttons on the page that can never be pressed. Widen
 * this list only when a language becomes a language the agent will answer the
 * phone in.
 */
const OPENING_LANGUAGES: readonly Language[] = ['en', 'ar', 'hi']

/**
 * Which languages can open a call, read from the file that decides it.
 *
 * docs/04- makes presence of copy in `data/disclosures.yaml` the readiness
 * signal for a language, "which makes the ship-Arabic-or-drop-it decision a
 * state of the repository rather than a note in a meeting". The language
 * selector therefore reads that file instead of carrying its own list, so it
 * cannot offer a language the agent would refuse to start in.
 *
 * A deliberately small reader rather than a YAML dependency: it needs one key
 * per opening language and whether each is empty. If this file ever grows
 * structure, take the dependency instead of growing the parser.
 */
export async function loadLanguageReadiness(): Promise<LanguageReadiness[]> {
  const path = join(process.cwd(), '..', 'data', 'disclosures.yaml')
  const text = await readFile(path, 'utf-8')
  return OPENING_LANGUAGES.map((language) => ({
    language,
    ready: hasCopy(text, language),
  }))
}

function hasCopy(text: string, language: Language): boolean {
  const lines = text.split('\n')
  const at = lines.findIndex((line) => new RegExp(`^${language}:`).test(line))
  if (at === -1) return false

  const inline = lines[at].slice(language.length + 1).trim()
  if (inline !== '' && inline !== '>' && inline !== '|' && inline !== '>-' && inline !== '|-') {
    // An inline value. Empty string literals are the "not ready" marker.
    return inline !== '""' && inline !== "''"
  }
  if (inline === '') return false

  // A block scalar: ready when at least one indented, non-comment line follows.
  for (let i = at + 1; i < lines.length; i += 1) {
    const line = lines[i]
    if (line.trim() === '') continue
    if (!/^\s/.test(line)) break
    if (line.trim().startsWith('#')) continue
    return true
  }
  return false
}
