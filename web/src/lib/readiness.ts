import 'server-only'

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { Language } from '@/lib/types'

export interface LanguageReadiness {
  language: Language
  /** A language with no disclosure copy cannot open a call (docs/04-). */
  ready: boolean
}

const LANGUAGES: readonly Language[] = ['en', 'ar', 'hi']

/**
 * Which languages can open a call, read from the file that decides it.
 *
 * docs/04- makes presence of copy in `data/disclosures.yaml` the readiness
 * signal for a language, "which makes the ship-Arabic-or-drop-it decision a
 * state of the repository rather than a note in a meeting". The language
 * selector therefore reads that file instead of carrying its own list, so it
 * cannot offer a language the agent would refuse to start in.
 *
 * A deliberately small reader rather than a YAML dependency: it needs three
 * keys and whether each is empty. If this file ever grows structure, take the
 * dependency instead of growing the parser.
 */
export async function loadLanguageReadiness(): Promise<LanguageReadiness[]> {
  const path = join(process.cwd(), '..', 'data', 'disclosures.yaml')
  const text = await readFile(path, 'utf-8')
  return LANGUAGES.map((language) => ({
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
