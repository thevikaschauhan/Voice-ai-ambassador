import 'server-only'

import { timingSafeEqual } from 'node:crypto'

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
