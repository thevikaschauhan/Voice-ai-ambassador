/**
 * The one place the web service writes a request line.
 *
 * WHY IT EXISTS: `next start` prints no access log, so before this module the
 * question "did a request reach web?" was unanswerable from web's own output -
 * its production log was ten startup lines and nothing since. The gap was found
 * the hard way, by a proxied call appearing in admin-api's log with no
 * counterpart in web's, which means an absence in web's log could not be
 * distinguished from an absence of requests.
 *
 * NO `server-only` IMPORT ON PURPOSE. `src/middleware.ts` runs in the Edge
 * runtime and `server-only` throws there, so this module stays free of it and
 * of anything Node-specific: `console.log` and `Date` are all it uses, and both
 * exist in both runtimes. That is also why the shape lives here once rather
 * than being written out at each call site - two hand-copied log shapes drift,
 * and docs/09-deploy.md quotes this one shape for both lines.
 *
 * THE ARGUMENTS ARE POSITIONAL PRIMITIVES, NOT AN OPTIONS OBJECT. That is the
 * safety property, and it is structural rather than a convention: a caller
 * cannot pass `{ ...request }` or slip in a `cookie` field, because there is no
 * field to slip it into. A request log is the easiest place in a web codebase
 * to leak a session cookie, a search term or a document body - the natural
 * implementation logs "the request", and a Request carries all three. Nothing
 * here can accept them.
 *
 * JSON one line per event, matching the Python services' stream so Railway
 * parses `event` as a field on all three (`railway logs --json`).
 */

/** Written by `src/middleware.ts` when an admin request arrives. */
export function logWebRequest(method: string, path: string): void {
  // `path` is a pathname, which by construction has no query string: the
  // caller passes `nextUrl.pathname`, never `nextUrl.href`.
  console.log(JSON.stringify({ ts: new Date().toISOString(), level: 'info', event: 'web_request', method, path }))
}

/**
 * Written by `src/lib/admin/proxy.ts` when a proxied call finishes.
 *
 * `route` is a key of `UPSTREAM_ROUTES`, not a pathname. The table is closed,
 * so this field is drawn from a fixed vocabulary and cannot carry a record id
 * or anything else the caller chose - a stronger guarantee than stripping a
 * query off `/api/admin/leads/<uuid>` and hoping the id is not interesting.
 *
 * `status` is what the caller was ACTUALLY answered with, including web's own
 * 401, 403, 502 and 503 refusals where no upstream call happened at all.
 * "Nothing reached the server" and "everything was refused at the door" are
 * different diagnoses and must not look identical in the log.
 */
export function logAdminProxy(
  method: string,
  route: string,
  status: number,
  durationMs: number,
): void {
  console.log(
    JSON.stringify({
      ts: new Date().toISOString(),
      level: 'info',
      event: 'admin_proxy',
      method,
      route,
      status,
      duration_ms: durationMs,
    }),
  )
}
