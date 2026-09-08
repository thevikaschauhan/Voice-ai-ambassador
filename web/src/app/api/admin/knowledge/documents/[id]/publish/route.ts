import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await params
  let body: unknown
  try { body = await request.json() } catch {
    return Response.json({ error: 'body must be JSON' }, { status: 400 })
  }
  return proxy(request, { route: 'documentPublish', id, method: 'POST', body, mutation: true })
}
