import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * The lead list (docs/10-: status, score, language, project ids, call time,
 * completeness, contact-present - no buyer words).
 *
 * The caller's filters are passed through as a query string, but only the keys
 * the contract names: an unfiltered pass-through would let a browser send
 * anything to a private service, which is the relay this design refuses to be.
 */
// Exactly what `admin_api.list_leads` declares today, plus the two docs/10-
// names that his `task-p2-admin-list-filters` will add. Forwarding `cursor`
// was a silent no-op: the API paginates on `offset`, and an unknown query
// parameter is ignored rather than refused, so the filter simply never applied.
const ALLOWED_FILTERS = ['status', 'language', 'project_id', 'limit', 'offset'] as const

export async function GET(request: Request): Promise<Response> {
  const asked = new URL(request.url).searchParams
  const kept = new URLSearchParams()
  for (const key of ALLOWED_FILTERS) {
    const value = asked.get(key)
    if (value !== null) kept.set(key, value)
  }
  const search = kept.toString()
  return proxy(request, { route: 'leads', search: search === '' ? '' : `?${search}` })
}
