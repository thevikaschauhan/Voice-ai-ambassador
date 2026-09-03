import 'server-only'

import { timingSafeEqual } from 'node:crypto'
import { TALK_LANGUAGES } from '@/lib/livekit/talk.shared'
import type { TalkLanguage } from '@/lib/livekit/talk.shared'

/**
 * Whether this deployment is the public, client-facing one, and who may in.
 *
 * `DEMO_ACCESS_CODE` does double duty and that is deliberate rather than thrifty:
 * its presence IS the hosted signal. A laptop demo has no access code because
 * the person demonstrating is standing there; the public URL has one because
 * nobody is. So one variable answers both "is anyone watching this" and "who
 * may start a call", and there is no second flag to set inconsistently.
 *
 * It is never a `NEXT_PUBLIC_` variable. A `NEXT_PUBLIC_` value is compiled
 * into the client bundle, where an access code is decoration (docs/09-).
 *
 * AN UNSET GATE IS A CLOSED GATE. That direction is the whole point: a
 * misconfigured public service that lets everyone in front of metered
 * providers is worse than one that lets nobody in, and the second failure is
 * the one that gets reported rather than billed.
 */

/** Long enough to be unguessable, short enough to read down a phone line. */
const MIN_CODE_LENGTH = 8

/**
 * Concurrent demo rooms allowed when `DEMO_MAX_ROOMS` says nothing.
 *
 * Two, not one: a visitor who closes the tab leaves a room behind until its
 * departure timeout expires, and a cap of one would lock the next visitor out
 * of a demo nobody is in. Not large, because every room is a metered call.
 */
const DEFAULT_MAX_ROOMS = 2

export function accessCode(): string | null {
  const code = process.env.DEMO_ACCESS_CODE?.trim()
  return code ? code : null
}

/** The public deployment, as opposed to a laptop. */
export function hosted(): boolean {
  return accessCode() !== null
}

export type GateResult = 'ok' | 'closed' | 'refused'

/**
 * Does this attempt open the gate?
 *
 * `closed` and `refused` are separated for the operator, not for the visitor:
 * both answer 403 with the same words, but only one of them means somebody
 * needs to set a variable.
 *
 * The comparison is constant time. It is not the strongest reason to do it -
 * an attacker who can time a network round trip against a short code has
 * easier options - but a credential compared with `===` is a habit that ends
 * up somewhere it matters, and `timingSafeEqual` costs nothing here.
 */
export function checkAccessCode(supplied: unknown): GateResult {
  const expected = accessCode()
  if (expected === null) return 'closed'
  if (typeof supplied !== 'string') return 'refused'

  const a = Buffer.from(supplied.trim(), 'utf8')
  const b = Buffer.from(expected, 'utf8')
  // timingSafeEqual throws on a length mismatch, which would leak the length
  // through an exception instead of through timing. Compare a fixed-size digest
  // of each side? No - simpler and just as good: compare only when the lengths
  // match, and spend the same work either way.
  const equal = a.length === b.length && timingSafeEqual(a, b)
  return equal ? 'ok' : 'refused'
}

/** Whether the configured code is long enough to be worth having. */
export function accessCodeIsWeak(): boolean {
  const code = accessCode()
  return code !== null && code.length < MIN_CODE_LENGTH
}

/**
 * Which languages the talk page offers, and why an empty answer is not one.
 *
 * `DEMO_LANGUAGES` is a comma-separated list of codes. UNSET means all three,
 * so a laptop is unchanged and nobody has to configure anything to demo in
 * Hindi at a venue. The hosted service sets it to `en` until the Arabic and
 * Hindi review packets come back, because offering a language whose copy
 * nobody has authored is offering a worse demo than not offering it.
 *
 * A value that is SET but yields no valid code falls back to ENGLISH ONLY, not
 * to all three, and the direction is the point. Somebody who typed this
 * variable meant to RESTRICT; honouring a typo by re-opening Arabic and Hindi
 * on a public URL would do the exact opposite of what they asked, and it would
 * do it silently. English is the one language with authored copy throughout, so
 * it is the safe floor. The mistake is logged, because a narrowed picker with
 * no explanation is its own small mystery.
 */
export function offeredLanguages(): TalkLanguage[] {
  const raw = process.env.DEMO_LANGUAGES?.trim()
  if (!raw) return [...TALK_LANGUAGES]

  const asked = raw.split(',').map((code) => code.trim().toLowerCase())
  // Filtered against the closed list rather than trusted, and ordered by that
  // list rather than by the variable, so the picker's order is a property of
  // the product instead of a property of how somebody typed an env var.
  const offered = TALK_LANGUAGES.filter((code) => asked.includes(code))
  if (offered.length > 0) return [...offered]

  console.warn(
    `talk: DEMO_LANGUAGES is set to ${JSON.stringify(raw)} but names no known ` +
      `language (${TALK_LANGUAGES.join(', ')}); offering English only, because a ` +
      `variable that was set meant to restrict`,
  )
  return ['en']
}

export function maxRooms(): number {
  const raw = process.env.DEMO_MAX_ROOMS?.trim()
  if (!raw) return DEFAULT_MAX_ROOMS
  const parsed = Number(raw)
  // An unparseable or absurd value falls back rather than throwing: a typo in
  // a service variable should not take the page down, and it must not read as
  // "unlimited" either.
  if (!Number.isInteger(parsed) || parsed < 1) return DEFAULT_MAX_ROOMS
  return parsed
}

export { DEFAULT_MAX_ROOMS, MIN_CODE_LENGTH }
