import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * Ask for a chunk's scope.
 *
 * A request, not a decision: `ambassador/knowledge.py` resolves it and may keep
 * the chunk admin-only regardless - a conflict with inventory overrides the
 * reviewer, and an unknown project is never publishable. The web tier does not
 * re-implement that closure; it sends the asked-for action and renders what
 * came back.
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
  return proxy(request, { route: 'chunkReviews', id, method: 'POST', body, mutation: true })
}
