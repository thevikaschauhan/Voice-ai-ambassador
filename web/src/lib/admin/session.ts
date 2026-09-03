import 'server-only'

import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'

/**
 * The admin session: a short-lived value the browser holds and cannot forge.
 *
 * Signed with `ADMIN_SESSION_SECRET`, which is deliberately NOT the access code
 * and not the upstream token (ADR-021). Three secrets doing one job each means
 * rotating the code a client was told over the phone does not invalidate the
 * bearer token, and a leaked session cannot be turned into either.
 *
 * HMAC-SHA256 from `node:crypto` rather than a JWT library, because the card
 * allows no new dependency without justification and there is none here: this
 * token has one issuer, one audience, one claim and no need to be read by
 * anything but this process. A JWT would add a parser, an algorithm-confusion
 * surface and a package, to carry a timestamp.
 */

/** Long enough for a working session, short enough to be worthless tomorrow. */
export const SESSION_MAX_AGE_S = 60 * 60 * 4

export const SESSION_COOKIE = 'admin_session'

export interface AdminSession {
  issuedAt: number
}

function secret(): string | null {
  const value = process.env.ADMIN_SESSION_SECRET?.trim()
  return value ? value : null
}

/**
 * `<issuedAt>.<nonce>.<signature>`.
 *
 * The nonce makes two sessions issued in the same millisecond different
 * strings, so one appearing in a log cannot be mistaken for another's.
 */
export function signAdminSession(session: AdminSession): string {
  const key = secret()
  if (key === null) {
    // Refusing to mint is the safe direction: a session signed with an empty
    // secret is a session anybody can sign.
    throw new Error('ADMIN_SESSION_SECRET is not set')
  }
  const nonce = randomBytes(8).toString('base64url')
  const payload = `${session.issuedAt}.${nonce}`
  return `${payload}.${createHmac('sha256', key).update(payload).digest('base64url')}`
}

/**
 * The session this request carries, or null - expired, forged and absent all
 * answer the same way, because the caller's response to each is 401 and
 * telling them which is telling them something.
 */
export function readAdminSession(cookieHeader: string | null): AdminSession | null {
  const key = secret()
  if (key === null || !cookieHeader) return null

  const raw = cookieHeader
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1)
  if (!raw) return null

  const parts = raw.split('.')
  if (parts.length !== 3) return null
  const [issued, nonce, signature] = parts

  const expected = createHmac('sha256', key).update(`${issued}.${nonce}`).digest('base64url')
  const a = Buffer.from(signature)
  const b = Buffer.from(expected)
  // Length-checked before the comparison: timingSafeEqual throws on a
  // mismatch, and an exception here would be a 500 instead of a 401.
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null

  const issuedAt = Number(issued)
  if (!Number.isFinite(issuedAt)) return null
  if (Date.now() - issuedAt > SESSION_MAX_AGE_S * 1000) return null
  return { issuedAt }
}

/** The Set-Cookie the login route returns. */
export function sessionCookie(value: string): string {
  return [
    `${SESSION_COOKIE}=${value}`,
    'Path=/',
    `Max-Age=${SESSION_MAX_AGE_S}`,
    'HttpOnly',
    'Secure',
    'SameSite=Strict',
  ].join('; ')
}

/** The Set-Cookie that ends a session. */
export function clearedSessionCookie(): string {
  return [
    `${SESSION_COOKIE}=`,
    'Path=/',
    'Max-Age=0',
    'HttpOnly',
    'Secure',
    'SameSite=Strict',
  ].join('; ')
}
