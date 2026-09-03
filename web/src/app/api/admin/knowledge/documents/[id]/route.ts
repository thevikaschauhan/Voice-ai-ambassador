import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** One document: its parse result, chunks and extracted figures. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params
  return proxy(request, { route: 'document', id })
}
