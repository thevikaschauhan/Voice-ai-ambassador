import { MAX_UPLOAD_BYTES } from '@/lib/admin/knowledge'
import { proxy } from '@/lib/admin/proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * A file on its way to the parser, capped before it is forwarded.
 *
 * The bytes are NOT inspected here: extraction is the Python adapter's, which
 * is where the PDF and DOCX libraries live and where page numbers and cell
 * order are preserved (docs/10- step 2, and this card's boundary). This route
 * checks the size and the field's presence, then streams the multipart body
 * through with the bearer added.
 *
 * The cap is enforced again here rather than trusted from the browser, because
 * a client-side check is a courtesy and a server-side one is a limit. The
 * upstream caps it a third time, which is the one that counts.
 */
export async function POST(request: Request): Promise<Response> {
  let form: FormData
  try {
    form = await request.formData()
  } catch {
    return Response.json({ error: 'expected a multipart upload' }, { status: 400 })
  }

  const file = form.get('file')
  if (!(file instanceof File)) {
    return Response.json({ error: 'no file was attached' }, { status: 400 })
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    // 413 rather than 400: the request was well formed and too big, and an
    // operator reading a log should be able to tell those apart.
    return Response.json(
      { error: `that file is larger than the ${MAX_UPLOAD_BYTES} byte limit` },
      { status: 413 },
    )
  }

  return proxy(request, { route: 'documentUpload', method: 'POST', form, mutation: true })
}
