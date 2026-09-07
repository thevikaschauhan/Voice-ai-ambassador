import 'server-only'

import { readAdminSession } from '@/lib/admin/session'
import { logAdminPageRead } from '@/lib/request-log'
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
  const started = Date.now()
  const { read, status } = await answer<T>(cookie, options)
  // EVERY path through `answer` is logged, including the refusal that never
  // reaches the upstream, because an absence in the log has to mean no page
  // read happened rather than "the read was refused at the door". `answer`
  // returns the status alongside the `PageRead` for exactly this reason: the
  // union it hands the page deliberately loses the code (a 404 and a 500 are
  // both `unavailable` to a renderer), and the code is the whole value of the
  // line. `options.route` is a key of the fixed upstream table, so this cannot
  // carry an id or a query the caller chose.
  logAdminPageRead(options.method ?? 'GET', options.route, status, Date.now() - started)
  return read
}

/**
 * The read itself. Unchanged behaviour; `readForPage` above only times it and
 * records what it answered.
 *
 * The status codes for the paths that never reach the upstream are the ones
 * `proxy()` uses for the same conditions, so a count or a filter over the log
 * does not need to know which emitter wrote the line.
 */
async function answer<T>(
  cookie: string | null,
  options: ForwardOptions,
): Promise<{ read: PageRead<T>; status: number }> {
  // The cookie is a PARAMETER, not read from Next's ambient request store.
  // `headers()` throws outside a request scope, so a reader that called it was
  // untestable and hid its own dependency; the caller has the header anyway.
  if (readAdminSession(cookie) === null) {
    return { read: { state: 'unauthenticated' }, status: 401 }
  }

  try {
    const response = await forward(options)
    if (!response.ok) {
      // The upstream's own status, described rather than forwarded: a 404 from
      // the API is "no such lead", not a broken page.
      return {
        read: {
          state: 'unavailable',
          reason:
            response.status === 404
              ? 'That lead does not exist.'
              : `The admin API answered ${response.status}.`,
        },
        status: response.status,
      }
    }
    return { read: { state: 'ok', data: (await response.json()) as T }, status: response.status }
  } catch (error) {
    if (error instanceof UpstreamNotConfigured) {
      console.error(`admin page: ${error.message}`)
      return {
        read: {
          state: 'unavailable',
          reason: 'The admin API is not configured for this deployment.',
        },
        status: 503,
      }
    }
    console.error(
      `admin page: upstream call failed: ${error instanceof Error ? error.message : 'unknown'}`,
    )
    return {
      read: { state: 'unavailable', reason: 'The admin API did not answer.' },
      status: 502,
    }
  }
}
