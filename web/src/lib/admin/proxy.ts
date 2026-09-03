import 'server-only'

import { readAdminSession } from '@/lib/admin/session'
import { UpstreamNotConfigured, forward } from '@/lib/admin/upstream'
import type { ForwardOptions } from '@/lib/admin/upstream'

/**
 * What every `/api/admin/*` route does before it forwards anything.
 *
 * One function rather than a check repeated per route, because a route that
 * forgets the session check is the whole vulnerability and there is no test
 * that notices a missing call in a file nobody added yet. Adding a route means
 * calling this; it is not possible to forward without it.
 */

/** Answers with no detail: which of these failed is not the caller's business. */
function refuse(status: number, reason: string): Response {
    return Response.json({ error: reason }, { status, headers: { 'cache-control': 'no-store' } })
}

/**
 * Same-origin check for mutations (docs/10-).
 *
 * `SameSite=Strict` is the first defence and it lives in the browser; this one
 * does not depend on the browser behaving. A mutation with no `Origin` header
 * at all is refused too - a same-origin fetch always sends one, so an absent
 * header means something that is not the page.
 *
 * COMPARED AGAINST THE HOST THE CLIENT ADDRESSED, not against `request.url`.
 * That distinction is not pedantry: behind a proxy the two diverge - the URL
 * carries the address the server is listening on and the `Host` header carries
 * the one the browser typed - and comparing them refuses every legitimate
 * mutation in production while passing every test on a laptop. Measured in the
 * container before this was written that way round: a same-origin decision POST
 * came back 403.
 *
 * `x-forwarded-proto` is honoured when the proxy sets it, so an `http` origin
 * against an `https` deployment is refused rather than matched on host alone.
 */
function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin')
  if (origin === null) return false

  const forwardedHost = request.headers.get('x-forwarded-host')?.split(',')[0].trim()
  const host = forwardedHost || request.headers.get('host')
  if (!host) return false

  let asked: URL
  try {
    asked = new URL(origin)
  } catch {
    return false
  }
  if (asked.host !== host) return false

  const forwardedProto = request.headers.get('x-forwarded-proto')?.split(',')[0].trim()
  if (forwardedProto && asked.protocol !== `${forwardedProto}:`) return false
  return true
}

export async function proxy(
  request: Request,
  options: ForwardOptions & { mutation?: boolean },
): Promise<Response> {
  if (readAdminSession(request.headers.get('cookie')) === null) {
    return refuse(401, 'sign in again')
  }
  if (options.mutation === true && !sameOrigin(request)) {
    return refuse(403, 'that request did not come from this page')
  }

  try {
    return await forward(options)
  } catch (error) {
    if (error instanceof UpstreamNotConfigured) {
      // An operator problem, not a caller problem, and the message names
      // neither the address nor the token.
      console.error(`admin proxy: ${error.message}`)
      return refuse(503, 'the admin API is not configured for this deployment')
    }
    console.error(
      `admin proxy: upstream call failed: ${error instanceof Error ? error.message : 'unknown'}`,
    )
    return refuse(502, 'the admin API did not answer')
  }
}
