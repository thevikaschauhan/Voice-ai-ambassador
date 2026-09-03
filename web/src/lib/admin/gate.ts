import 'server-only'

import { timingSafeEqual } from 'node:crypto'

/**
 * Who may reach `/admin`, and how often they may guess.
 *
 * The same pattern as the demo gate (`lib/hosted.ts`), deliberately: unset is
 * CLOSED, the comparison is constant time with a length guard, and the two
 * failure reasons are one answer to the caller. A second implementation of
 * this shape would be a second thing to get wrong, so the reasoning is shared
 * even though the variable is not.
 *
 * `ADMIN_ACCESS_CODE` is separate from `DEMO_ACCESS_CODE` on purpose: the demo
 * code is read out to a client, and the admin code opens the lead database.
 */

/** Attempts allowed in a window before the door stops answering. */
const MAX_ATTEMPTS = 8

/** How long a window lasts. */
const WINDOW_MS = 5 * 60 * 1000

export type AdminGateResult = 'ok' | 'closed' | 'refused' | 'rate_limited'

/**
 * Attempts in the current window.
 *
 * Process-local and deliberately not a store: one web service, one shared
 * code, and a limiter that survives a restart would need somewhere to live
 * that ADR-021 says this tier does not have (no database access from web). A
 * restart resets the count, which is a real limit of this and is the cost of
 * not putting state here.
 */
let attempts: { count: number; until: number } = { count: 0, until: 0 }

export function adminCodeConfigured(): boolean {
  return (process.env.ADMIN_ACCESS_CODE?.trim() ?? '') !== ''
}

export function checkAdminCode(supplied: unknown): AdminGateResult {
  const now = Date.now()
  if (now > attempts.until) attempts = { count: 0, until: now + WINDOW_MS }
  if (attempts.count >= MAX_ATTEMPTS) return 'rate_limited'

  const expected = process.env.ADMIN_ACCESS_CODE?.trim() ?? ''
  if (expected === '') {
    // Counted as an attempt even though nothing could have matched: an unset
    // gate on a public URL should not be a free guessing surface either.
    attempts.count += 1
    return 'closed'
  }
  if (typeof supplied !== 'string') {
    attempts.count += 1
    return 'refused'
  }

  const a = Buffer.from(supplied.trim(), 'utf8')
  const b = Buffer.from(expected, 'utf8')
  const equal = a.length === b.length && timingSafeEqual(a, b)
  if (!equal) {
    attempts.count += 1
    return 'refused'
  }
  // A correct code clears the window, so one operator fat-fingering their code
  // twice does not lock themselves out for five minutes after getting it right.
  attempts = { count: 0, until: now + WINDOW_MS }
  return 'ok'
}

/** For tests, and for nothing else: the limiter is process state. */
export function resetAdminRateLimit(): void {
  attempts = { count: 0, until: 0 }
}

export { MAX_ATTEMPTS, WINDOW_MS }
