import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * Approve or revoke ONE extracted occurrence.
 *
 * A mutation, so it carries the origin check as well as the session. Reviews
 * are append-only (docs/02-): revoking does not delete an approval, it records
 * the opposite, which is why there is no DELETE here.
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
  return proxy(request, { route: 'figureReviews', id, method: 'POST', body, mutation: true })
}
