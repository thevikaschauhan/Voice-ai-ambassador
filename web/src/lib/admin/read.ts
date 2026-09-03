import 'server-only'

import { readAdminSession } from '@/lib/admin/session'
import { UpstreamNotConfigured, forward } from '@/lib/admin/upstream'
import type { ForwardOptions } from '@/lib/admin/upstream'

/**
 * What an admin PAGE does to read from the API.
 *
 * A server component calls `forward` directly rather than fetching its own
 * `/api/admin/*` proxy: the proxy exists for the browser, and a page making an
 * HTTP round trip to itself to reach a service it can already reach is a hop
 * that can fail on its own. Both paths go through the one upstream module, so
 * there is still exactly one reader of the token.
 *
 * The session is checked here for the same reason `proxy()` checks it: a page
 * that renders lead data without checking is the whole vulnerability, and this
 * makes the check impossible to forget rather than merely documented.
 */
export type PageRead<T> =
  | { state: 'ok'; data: T }
  | { state: 'unauthenticated' }
  | { state: 'unavailable'; reason: string }

export async function readForPage<T>(
  cookie: string | null,
  options: ForwardOptions,
): Promise<PageRead<T>> {
  // The cookie is a PARAMETER, not read from Next's ambient request store.
  // `headers()` throws outside a request scope, so a reader that called it was
  // untestable and hid its own dependency; the caller has the header anyway.
  if (readAdminSession(cookie) === null) return { state: 'unauthenticated' }

  try {
    const response = await forward(options)
    if (!response.ok) {
      // The upstream's own status, described rather than forwarded: a 404 from
      // the API is "no such lead", not a broken page.
      return {
        state: 'unavailable',
        reason:
          response.status === 404
            ? 'That lead does not exist.'
            : `The admin API answered ${response.status}.`,
      }
    }
    return { state: 'ok', data: (await response.json()) as T }
  } catch (error) {
    if (error instanceof UpstreamNotConfigured) {
      console.error(`admin page: ${error.message}`)
      return {
        state: 'unavailable',
        reason: 'The admin API is not configured for this deployment.',
      }
    }
    console.error(
      `admin page: upstream call failed: ${error instanceof Error ? error.message : 'unknown'}`,
    )
    return { state: 'unavailable', reason: 'The admin API did not answer.' }
  }
}
