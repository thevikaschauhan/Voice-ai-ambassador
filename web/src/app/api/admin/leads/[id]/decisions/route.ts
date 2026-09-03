import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * Append a qualify or reject decision.
 *
 * A mutation, so it carries the origin check as well as the session: decisions
 * are append-only and audited (ADR-020), and an appended decision cannot be
 * taken back by refusing the next one.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return Response.json({ error: 'body must be JSON' }, { status: 400 })
  }
  return proxy(request, { route: 'leadDecisions', id, method: 'POST', body, mutation: true })
}
