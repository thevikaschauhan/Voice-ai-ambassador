import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** The document list, and creating one from pasted text or an upload. */
export async function GET(request: Request): Promise<Response> {
  return proxy(request, { route: 'documents' })
}

export async function POST(request: Request): Promise<Response> {
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return Response.json({ error: 'body must be JSON' }, { status: 400 })
  }
  return proxy(request, { route: 'documents', method: 'POST', body, mutation: true })
}
