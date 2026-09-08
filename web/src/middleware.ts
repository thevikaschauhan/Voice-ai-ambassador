import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

import { logWebRequest } from '@/lib/request-log'

/**
 * One line per admin request that ARRIVES, and nothing else.
 *
 * The proxy (`src/lib/admin/proxy.ts`) records how a `/api/admin/*` call
 * FINISHED, with its status and duration. It cannot record a request that never
 * got that far - a mistyped path that 404s in the router, or a page request,
 * which is a server component and so cannot see its own status. This is the
 * arrival half. An `/api/admin/*` request therefore produces two lines, an
 * arrival and an outcome, and they are told apart by `event`
 * (`web_request` / `admin_proxy`), which is what makes them countable
 * separately in `railway logs --json`.
 *
 * SCOPED TO THE ADMIN SURFACE. The matcher is the scope of the whole feature:
 * narrower and the gap stays open on exactly the routes this was built for,
 * wider and every public demo page view is logged, which is both noise and a
 * privacy question nobody asked for. `tests/web-request-log.test.ts` asserts
 * both bounds so a later widening has to be deliberate.
 *
 * It logs and gets out of the way: no rewrite, no redirect, no header SET. The
 * `/admin` gate lives in the page and the proxy, not here - a middleware that
 * also enforces auth is a second copy of the rule, and the two copies drift.
 */

/**
 * Next's router-prefetch request headers, by name, from Next 16.3.4
 * `client/components/app-router-headers.js`.
 *
 * `rsc` is deliberately NOT in this list. It marks any RSC request, a
 * navigation included, so using it would report every client-side navigation
 * as a prefetch - wrong in exactly the case the flag exists to settle.
 */
const PREFETCH_HEADERS = ['next-router-prefetch', 'next-router-segment-prefetch'] as const

/**
 * PRESENCE ONLY, NEVER THE VALUE. This is the whole of the header exception
 * that `lib/request-log.ts` records: two names are tested for presence, and a
 * boolean leaves this function. `has` cannot return anything a header carries.
 */
function isPrefetch(request: NextRequest): boolean {
  return PREFETCH_HEADERS.some((name) => request.headers.has(name))
}

export function middleware(request: NextRequest): NextResponse {
  // `nextUrl.pathname` and not `nextUrl.href`: the pathname carries no query
  // string, so a search term cannot reach the log by construction rather than
  // by being stripped later.
  logWebRequest(request.method, request.nextUrl.pathname, isPrefetch(request))
  return NextResponse.next()
}

export const config = {
  matcher: ['/admin/:path*', '/api/admin/:path*'],
}
