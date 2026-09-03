// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The web tier against the admin API's REAL shapes (task-p2-web-drift-fixes).
 *
 * Every fixture below is copied from `adapter/admin_api.py` and
 * `adapter/repository.py` on main rather than from what this tier hoped for -
 * which is the whole point, because four of these differences are the reason
 * the deployed /admin renders empty lists and cannot record a decision.
 *
 * The drift, for the record:
 *   1. GET /v1/leads and GET /v1/knowledge/documents return BARE LISTS
 *      (`-> list[dict]`), not `{leads: [...]}` / `{documents: [...]}`.
 *   2. DecisionRequest expects `expected_lead_revision`, not `revision`.
 *   3. The lead list sends `contact_status`, not `contact_present`.
 *   4. The list takes `offset`, not `cursor`.
 *   5. Figures come back at the DOCUMENT level, ordered by chunk_id, not
 *      nested per chunk.
 */

/** Exactly the columns `repository.list_leads` names, in its order. */
const REAL_LEAD_ROW = {
  id: 'a3f1c2d4-0000-4000-8000-000000000001',
  session_id: 'sess-a1b2',
  created_at: '2026-09-03T09:00:00+00:00',
  ended_at: '2026-09-03T09:07:30+00:00',
  call_end_reason: 'buyer_farewell',
  ended_cleanly: true,
  language: 'en',
  requested_language: 'en',
  uncertified_fallback: false,
  analysis_status: 'complete',
  score_total: 61,
  score_version: 'v1',
  status: 'unreviewed',
  revision: 3,
  contact_status: 'captured',
}

/** `get_document` puts chunks and figures on the document, side by side. */
const REAL_DOCUMENT = {
  id: 'doc-1',
  revision: 1,
  title: 'Skyrise brochure',
  source_type: 'pdf',
  status: 'draft',
  parse_error_code: null,
  created_at: '2026-09-03T09:00:00+00:00',
  published_at: null,
  chunks: [
    {
      id: 'chunk-1',
      document_id: 'doc-1',
      document_revision: 1,
      ordinal: 0,
      heading: 'Payment plans',
      body: 'Two bedroom residences start at AED 2,000,000.',
      retrieval_scope: 'admin_only',
      project_id: null,
      scope_review_id: null,
      conflict_code: null,
      page_start: 4,
      page_end: 4,
    },
    {
      id: 'chunk-2',
      document_id: 'doc-1',
      document_revision: 1,
      ordinal: 1,
      heading: 'Prices',
      body: 'The price list is restated here.',
      retrieval_scope: 'inventory_governed',
      project_id: null,
      scope_review_id: null,
      conflict_code: 'conflicts_with_inventory',
      page_start: 5,
      page_end: 5,
    },
  ],
  figures: [
    {
      id: 'fig-1',
      chunk_id: 'chunk-1',
      value: '2000000',
      kind: 'amount',
      currency: 'AED',
      unit: null,
      surface: 'AED 2,000,000',
      source_sentence: 'Two bedroom residences start at AED 2,000,000.',
      page: 4,
      active_approval_id: null,
    },
    {
      id: 'fig-3',
      chunk_id: 'chunk-2',
      value: '985000',
      kind: 'amount',
      currency: 'AED',
      unit: null,
      surface: 'AED 985,000',
      source_sentence: 'The price list is restated here at AED 985,000.',
      page: 5,
      active_approval_id: 'appr-1',
    },
  ],
}

function stubUpstream(payload: unknown, status = 200) {
  const calls: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
      })
      return new Response(JSON.stringify(payload), {
        status,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch,
  )
  return calls
}

beforeEach(() => {
  vi.resetModules()
  process.env.ADMIN_ACCESS_CODE = 'an-admin-code-long-enough'
  process.env.ADMIN_SESSION_SECRET = 'a-session-secret-long-enough'
  process.env.ADMIN_API_TOKEN = 'stub-upstream-token'
  process.env.ADMIN_API_URL = 'http://admin-api.railway.internal:8080'
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete process.env.ADMIN_ACCESS_CODE
  delete process.env.ADMIN_SESSION_SECRET
  delete process.env.ADMIN_API_TOKEN
  delete process.env.ADMIN_API_URL
})

async function session(): Promise<string> {
  const { signAdminSession } = await import('@/lib/admin/session')
  return `admin_session=${signAdminSession({ issuedAt: Date.now() })}`
}

describe('the lead list against the real response', () => {
  it('reads a bare list rather than an envelope', async () => {
    stubUpstream([REAL_LEAD_ROW])
    const { readLeadRows } = await import('@/lib/admin/leads.server')
    const rows = await readLeadRows(new Request('https://demo.example/admin/leads', { headers: { cookie: await session() } }))
    expect(rows.state).toBe('ok')
    if (rows.state !== 'ok') return
    expect(rows.data).toHaveLength(1)
    expect(rows.data[0].session_id).toBe('sess-a1b2')
  })

  it('derives contact_present from the contact_status the API sends', async () => {
    stubUpstream([REAL_LEAD_ROW, { ...REAL_LEAD_ROW, id: 'b', contact_status: 'declined' }])
    const { readLeadRows } = await import('@/lib/admin/leads.server')
    const rows = await readLeadRows(new Request('https://demo.example/admin/leads', { headers: { cookie: await session() } }))
    if (rows.state !== 'ok') throw new Error('expected ok')
    // docs/10- asks the list to show contact-PRESENT; the API sends the status,
    // which is strictly more information. Deriving beats asking toby to add a
    // boolean that is already implied.
    expect(rows.data[0].contact_present).toBe(true)
    expect(rows.data[1].contact_present).toBe(false)
  })
})

describe('the lead list query', () => {
  it('passes offset, which is what the API paginates on', async () => {
    const calls = stubUpstream([REAL_LEAD_ROW])
    const { GET } = await import('@/app/api/admin/leads/route')
    await GET(
      new Request('https://demo.example/api/admin/leads?status=unreviewed&offset=50&cursor=nope', {
        headers: { cookie: await session() },
      }),
    )
    expect(calls[0].url).toContain('offset=50')
    expect(calls[0].url).toContain('status=unreviewed')
    // `cursor` is not a parameter the API has; forwarding it was a silent no-op.
    expect(calls[0].url).not.toContain('cursor')
  })
})

describe('the document detail against the real response', () => {
  it('reads a bare list of documents', async () => {
    stubUpstream([{ ...REAL_DOCUMENT, chunks: undefined, figures: undefined }])
    const { readDocumentRows } = await import('@/lib/admin/knowledge.server')
    const rows = await readDocumentRows(new Request('https://demo.example/admin/knowledge', { headers: { cookie: await session() } }))
    if (rows.state !== 'ok') throw new Error('expected ok')
    expect(rows.data).toHaveLength(1)
    expect(rows.data[0].title).toBe('Skyrise brochure')
  })

  it('groups document-level figures onto their chunks by chunk_id', async () => {
    stubUpstream(REAL_DOCUMENT)
    const { readDocument } = await import('@/lib/admin/knowledge.server')
    const read = await readDocument(
      new Request('https://demo.example/admin/knowledge/doc-1', {
        headers: { cookie: await session() },
      }),
      'doc-1',
    )
    if (read.state !== 'ok') throw new Error('expected ok')
    const [first, second] = read.data.chunks
    // One query upstream, ordered by chunk_id; the grouping is this tier's.
    expect(first.figures.map((figure) => figure.id)).toEqual(['fig-1'])
    expect(second.figures.map((figure) => figure.id)).toEqual(['fig-3'])
  })

  it('keeps a figure whose chunk is missing rather than dropping it silently', async () => {
    stubUpstream({
      ...REAL_DOCUMENT,
      figures: [...REAL_DOCUMENT.figures, { ...REAL_DOCUMENT.figures[0], id: 'fig-9', chunk_id: 'gone' }],
    })
    const { readDocument } = await import('@/lib/admin/knowledge.server')
    const read = await readDocument(
      new Request('https://demo.example/admin/knowledge/doc-1', {
        headers: { cookie: await session() },
      }),
      'doc-1',
    )
    if (read.state !== 'ok') throw new Error('expected ok')
    const shown = read.data.chunks.flatMap((chunk) => chunk.figures.map((f) => f.id))
    // A figure that groups nowhere is a figure nobody reviews, and an
    // unreviewed figure is unspeakable - so losing it is safe but silent, and
    // silent is what makes it a bug next time.
    expect(read.data.orphanFigures.map((f) => f.id)).toEqual(['fig-9'])
    expect(shown).not.toContain('fig-9')
  })
})
