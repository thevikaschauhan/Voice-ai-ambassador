import 'server-only'

/**
 * The only module that knows the admin API's address or its bearer token.
 *
 * One reader, asserted structurally in `tests/admin-gate.test.ts`, for the
 * reason the `canPublish: true` guard exists: a secret with one reader can be
 * reviewed by reading one file, and a secret with three is a search problem
 * that gets one grep wrong.
 *
 * THE BROWSER NEVER CHOOSES AN UPSTREAM. Every route below names a path from
 * the fixed table, and `forward` will not accept anything else - so a request
 * cannot turn these proxies into an open relay against the Railway private
 * network (docs/10-, ADR-021). That is why the table is a table and not a
 * `[...path]` catch-all, which is the obvious shape and the wrong one.
 */

/**
 * The routes this surface may reach, from docs/10-'s contract.
 *
 * Adding a UI card adds an entry here, not a mechanism. `:id` is the only
 * dynamic part and it is encoded before it is interpolated, so an id cannot
 * carry a path of its own.
 */
export const UPSTREAM_ROUTES = {
  leads: '/v1/leads',
  lead: '/v1/leads/:id',
  leadDecisions: '/v1/leads/:id/decisions',
  documents: '/v1/knowledge/documents',
  // A separate path because FastAPI cannot bind a JSON body and a
  // multipart form on one handler (measured while building the route).
  documentUpload: '/v1/knowledge/documents/upload',
  document: '/v1/knowledge/documents/:id',
  chunkReviews: '/v1/knowledge/chunks/:id/reviews',
  figureReviews: '/v1/knowledge/figures/:id/reviews',
  ready: '/ready',
} as const

export type UpstreamRoute = keyof typeof UPSTREAM_ROUTES

export class UpstreamNotConfigured extends Error {}

/** How long the admin UI waits for the API before saying so. */
const TIMEOUT_MS = 15_000

function baseUrl(): string {
  const url = process.env.ADMIN_API_URL?.trim()
  if (!url) throw new UpstreamNotConfigured('ADMIN_API_URL is not set')
  return url.replace(/\/$/, '')
}

function token(): string {
  const value = process.env.ADMIN_API_TOKEN?.trim()
  if (!value) throw new UpstreamNotConfigured('ADMIN_API_TOKEN is not set')
  return value
}

export interface ForwardOptions {
  route: UpstreamRoute
  /** Fills `:id`. Encoded here, so it cannot smuggle a path segment. */
  id?: string
  method?: 'GET' | 'POST'
  /** Verbatim query string from the caller's URL, keys filtered by the caller. */
  search?: string
  body?: unknown
  /**
   * A multipart body to stream through unchanged, for a file upload.
   *
   * Separate from `body` because it must NOT be JSON-serialised and its
   * content-type carries a generated boundary - setting one by hand produces a
   * request the upstream cannot parse. The bytes are not inspected here: parsing
   * is the Python adapter's (docs/10- step 2).
   */
  form?: FormData
}

/**
 * Call the admin API with the bearer added here.
 *
 * Returns the upstream's status and JSON as-is. It does NOT pass the upstream's
 * headers back: they are the private service's, and copying them is how an
 * internal address or a `Server:` banner ends up in a browser.
 */
export async function forward(options: ForwardOptions): Promise<Response> {
  const template = UPSTREAM_ROUTES[options.route]
  const path = template.replace(':id', encodeURIComponent(options.id ?? ''))
  const url = `${baseUrl()}${path}${options.search ?? ''}`

  const response = await fetch(url, {
    method: options.method ?? 'GET',
    headers: {
      authorization: `Bearer ${token()}`,
      accept: 'application/json',
      // No content-type for a multipart body: fetch generates one with the
      // boundary, and overriding it makes the upstream unable to parse it.
      ...(options.form !== undefined || options.body === undefined
        ? {}
        : { 'content-type': 'application/json' }),
    },
    body:
      options.form ??
      (options.body === undefined ? undefined : JSON.stringify(options.body)),
    signal: AbortSignal.timeout(TIMEOUT_MS),
    cache: 'no-store',
  })

  const text = await response.text()
  return new Response(text === '' ? null : text, {
    status: response.status,
    headers: {
      'content-type': response.headers.get('content-type') ?? 'application/json',
      'cache-control': 'no-store',
    },
  })
}

export { TIMEOUT_MS }
