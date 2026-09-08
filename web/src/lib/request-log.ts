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
 * NO HEADER IS EVER RECORDED, WITH ONE NARROW EXCEPTION WRITTEN HERE BESIDE THE
 * RULE IT NARROWS. `logWebRequest` takes a `prefetch` boolean derived from the
 * presence of Next's two router-prefetch request headers and nothing else. The
 * header VALUE is never read into the line, no other header is ever consulted,
 * and the field is a boolean rather than a string so there is nothing for a
 * value to travel in. Recording a derived boolean is not recording the header.
 *
 * The exception was granted because without it every count of page views taken
 * from this log is wrong by the prefetch factor: an arrival is not a render,
 * and on 2026-09-07 the same visit produced 16 arrivals at a detail route and
 * 0 renders of it. See `tests/web-request-log.test.ts` and docs/09-deploy.md's
 * coverage map, which both carry the exception too.
 *
 * JSON one line per event, matching the Python services' stream so Railway
 * parses `event` as a field on all three (`railway logs --json`).
 */

/**
 * Written by `src/middleware.ts` when an admin request arrives.
 *
 * `prefetch` says whether the arrival was Next prefetching the route rather
 * than a person navigating to it. It is recorded as `false` and not omitted
 * when it is false: a missing field is what every line written before this
 * shipped looks like, and a count that treats the two alike folds the old
 * lines silently into "navigation".
 */
export function logWebRequest(method: string, path: string, prefetch: boolean): void {
  // `path` is a pathname, which by construction has no query string: the
  // caller passes `nextUrl.pathname`, never `nextUrl.href`.
  console.log(
    JSON.stringify({
      ts: new Date().toISOString(),
      level: 'info',
      event: 'web_request',
      method,
      path,
      prefetch,
    }),
  )
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

/**
 * Written by `src/lib/admin/read.ts` when a page read finishes.
 *
 * A THIRD EVENT RATHER THAN A WIDER `admin_proxy`, and the distinction is not
 * cosmetic. An admin PAGE does not fetch its own `/api/admin/*` route: `read.ts`
 * calls `forward` directly, because a page making an HTTP round trip to itself
 * to reach a service it can already reach is a hop that can fail on its own. So
 * a page read never passes `proxy()` and was invisible to `admin_proxy` - found
 * when web's one `admin_proxy` line disagreed with admin-api's ten access lines
 * over the same window. Widening `admin_proxy` would have fixed the count going
 * forward and silently rewritten the meaning of every count already taken from
 * it; a new name leaves the old series intact and comparable.
 *
 * `status` is what the read was ANSWERED with, refusals included, on the same
 * vocabulary `proxy()` uses (401 no session, 503 unconfigured, 502 no answer,
 * otherwise the upstream's own code). There is no 403: a page read is not a
 * mutation, so the same-origin check does not apply to it.
 */
export function logAdminPageRead(
  method: string,
  route: string,
  status: number,
  durationMs: number,
): void {
  console.log(
    JSON.stringify({
      ts: new Date().toISOString(),
      level: 'info',
      event: 'admin_page_read',
      method,
      route,
      status,
      duration_ms: durationMs,
    }),
  )
}
