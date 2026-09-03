/**
 * The languages a visitor may pick, and the type that goes with them.
 *
 * Split out of `talk.ts` because that file is `server-only` - it mints tokens -
 * while the picker on the page and the gate in `hosted.ts` both need this list.
 * Same split as `ambassador.shared.ts`, so the server-only marker keeps meaning
 * something.
 *
 * A closed list, checked rather than trusted from the request: the chosen value
 * is serialised into room metadata that the worker reads and builds a voice and
 * an STT model from, so an unchecked string from a browser would reach the
 * agent's configuration. `data/` is the authority on what is certified to be
 * spoken; this is the authority on what may be asked for; and `DEMO_LANGUAGES`
 * narrows THIS list to what a given deployment offers today.
 */
export const TALK_LANGUAGES = ['en', 'ar', 'hi'] as const
export type TalkLanguage = (typeof TALK_LANGUAGES)[number]

export function isTalkLanguage(value: unknown): value is TalkLanguage {
  return typeof value === 'string' && (TALK_LANGUAGES as readonly string[]).includes(value)
}
