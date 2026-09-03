import { MAX_UPLOAD_BYTES } from '@/lib/admin/knowledge'
import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** The document list, and creating one from pasted text or an upload. */
export async function GET(request: Request): Promise<Response> {
  return proxy(request, { route: 'documents' })
}

/**
 * Pasted text on its way to the parser, capped before it is forwarded.
 *
 * The API caps it too and that is the gate - god found this door open there,
 * where 8388609 bytes returned 201 and were chunked into Postgres. Refusing
 * here as well saves a reviewer posting 8MB to be told no, and matches what
 * the upload route beside this one already did.
 *
 * Measured in BYTES, like the API: Arabic and a curly quote are two or three
 * bytes each, so counting characters would admit several times the cap.
 */
export async function POST(request: Request): Promise<Response> {
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return Response.json({ error: 'body must be JSON' }, { status: 400 })
  }

  const text = (body as { text?: unknown } | null)?.text
  if (typeof text === 'string' && Buffer.byteLength(text, 'utf8') > MAX_UPLOAD_BYTES) {
    // 413 rather than 400, and the same status the upstream answers: the
    // request was well formed and too big.
    return Response.json(
      { error: `that text is larger than the ${MAX_UPLOAD_BYTES} byte limit` },
      { status: 413 },
    )
  }

  return proxy(request, { route: 'documents', method: 'POST', body, mutation: true })
}
