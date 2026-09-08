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

/**
 * The lead DETAIL, captured from a running admin API rather than written from
 * what this tier hoped for (task-web-lead-detail-score).
 *
 * Captured by sealing two turns and a decision note with `adapter.crypto`'s
 * own Sealer, inserting them, and reading
 * `GET /v1/leads/<id>` back through uvicorn. Four differences from what
 * `readLead` assumes, and every one of them is a live defect:
 *
 *   1. THERE IS NO `score` OBJECT. The API sends `score_total`,
 *      `score_version` and `score_breakdown` as three flat fields -
 *      `admin_api.py` never assembles one - so `upstream.score` is always
 *      undefined and the detail has never shown a score.
 *   2. `score_breakdown` ARRIVES AS A JSON STRING. `get_lead` does
 *      `SELECT *` and asyncpg hands jsonb back as text; the repository calls
 *      `json.loads` by hand for `chunk_refs` and nowhere else.
 *   3. A TURN carries `payload`, a JSON STRING of the sealed turn model, plus
 *      `payload_error`. There is no `speaker` and no `text` at the top level,
 *      which is where `LeadTurnView` looks for them.
 *   4. A DECISION carries `created_at`, NOT `decided_at`. `decided_at` exists
 *      only in this tier's own type - which is why the render's
 *      `decision.decided_at.slice(0, 16)` throws and the page 500s for any
 *      lead that has ever been qualified or rejected.
 */
const REAL_LEAD_DETAIL = {
  id: '33333333-3333-3333-3333-333333333333',
  session_id: 'sess-score',
  created_at: '2026-09-06T09:00:00Z',
  ended_at: '2026-09-06T09:07:30Z',
  call_end_reason: 'buyer_farewell',
  ended_cleanly: true,
  language: 'en',
  requested_language: 'en',
  uncertified_fallback: false,
  inventory_version: 'inv-1',
  ambassador_name: '',
  project_ids: [],
  retention_expires_at: null,
  analysis_status: 'complete',
  status: 'unreviewed',
  revision: 3,
  brief: null,
  brief_error: null,
  summary: null,
  summary_error: null,
  score_total: 61,
  score_version: 'v1',
  // A STRING, not an array. Copied byte for byte from the response.
  score_breakdown:
    '[{"signal": "budget_stated", "observed": true, "raw_value": 2000000, "points_awarded": 15, "max_points": 15, "evidence_turn_indexes": [4]}, {"signal": "timeline_stated", "observed": false, "raw_value": false, "points_awarded": 0, "max_points": 10, "evidence_turn_indexes": []}]',
  contact: {
    status: 'not_asked',
    asked_turn_index: null,
    source_turn_index: null,
    permission: false,
    confirmed: false,
    phone_fingerprint: null,
    email_fingerprint: null,
    name: null,
    phone: null,
    email: null,
  },
  turns: [
    {
      lead_id: '33333333-3333-3333-3333-333333333333',
      turn_index: 4,
      timestamp: '2026-09-06T09:01:00Z',
      audit_incomplete: false,
      payload:
        '{"turn_index": 4, "speaker": "buyer", "text": "My budget is two million dirhams.", "timestamp": "2026-09-06T09:01:00Z", "audit_incomplete": false}',
      payload_error: null,
    },
    {
      lead_id: '33333333-3333-3333-3333-333333333333',
      turn_index: 5,
      timestamp: '2026-09-06T09:01:00Z',
      audit_incomplete: false,
      payload:
        '{"turn_index": 5, "speaker": "agent", "text": "Understood.", "timestamp": "2026-09-06T09:01:00Z", "audit_incomplete": false}',
      payload_error: null,
    },
  ],
  decisions: [
    {
      id: 'fba4a2b8-fc34-4f97-8f2d-fa5db4b3467f',
      lead_id: '33333333-3333-3333-3333-333333333333',
      sequence: 1,
      previous_status: 'unreviewed',
      new_status: 'rejected',
      reason_code: 'follow_up',
      note: 'called back later',
      actor_kind: 'admin',
      actor_id: null,
      // `created_at`, and there is no `decided_at` anywhere in the response.
      created_at: '2026-09-06T10:00:00Z',
      expected_lead_revision: 3,
      note_error: null,
    },
  ],
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

describe('the lead detail against the real response', () => {
  async function detail() {
    stubUpstream(REAL_LEAD_DETAIL)
    const { readLead } = await import('@/lib/admin/leads.server')
    const read = await readLead(
      new Request('https://demo.example/admin/leads/x', {
        headers: { cookie: await session() },
      }),
      REAL_LEAD_DETAIL.id,
    )
    if (read.state !== 'ok') throw new Error(`expected ok, got ${read.state}`)
    return read.data
  }

  it('assembles the score from the three fields the API actually sends', async () => {
    // There is no `score` object in the response and never has been, so
    // `upstream.score` is undefined and every detail page has rendered "No
    // score: the analysis has not completed" for a completed analysis.
    const lead = await detail()
    expect(lead.score).not.toBeNull()
    expect(lead.score?.total).toBe(61)
    expect(lead.score?.score_version).toBe('v1')
  })

  it('parses the breakdown, which arrives as a JSON string', async () => {
    // asyncpg hands jsonb back as text and `get_lead` does `SELECT *` with no
    // codec, so this is a string on the wire. docs/10 "Interest score" makes
    // the per-signal evidence the thing that separates a reviewable score from
    // an asserted one, so it is the breakdown that matters here, not the total.
    const lead = await detail()
    expect(lead.score?.breakdown).toHaveLength(2)
    expect(lead.score?.breakdown[0].signal).toBe('budget_stated')
    expect(lead.score?.breakdown[0].points_awarded).toBe(15)
    expect(lead.score?.breakdown[0].evidence_turn_indexes).toEqual([4])
    // The signal that scored nothing is kept, or the total cannot be
    // reconciled with the rows under it.
    expect(lead.score?.breakdown[1].observed).toBe(false)
  })

  it('reads a turn out of the payload string it arrives in', async () => {
    // The API sends the sealed turn model as a JSON string in `payload`;
    // `speaker` and `text` are inside it, not on the turn.
    const lead = await detail()
    expect(lead.turns).toHaveLength(2)
    expect(lead.turns[0].text).toBe('My budget is two million dirhams.')
    expect(lead.turns[0].speaker).toBe('buyer')
    expect(lead.turns[0].turn_index).toBe(4)
  })

  it('takes the decision timestamp from created_at, which is the one sent', async () => {
    // `decided_at` exists only in this tier's own type. The render does
    // `decision.decided_at.slice(0, 16)`, so undefined here is not a missing
    // timestamp - it is a TypeError and a 500 on the whole page, for any lead
    // that has ever been qualified or rejected.
    const lead = await detail()
    expect(lead.decisions).toHaveLength(1)
    expect(lead.decisions[0].decided_at).toBe('2026-09-06T10:00:00Z')
    expect(lead.decisions[0].note).toBe('called back later')
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

/**
 * A MARKDOWN document, captured from a REAL admin-api at b458f04.
 *
 * Not typed from `DocumentRow` and not written from the card: uploaded as a
 * synthetic NOTAREAL .md through the real `POST /v1/knowledge/documents/upload`
 * against a local admin-api at schema 0005, then copied out of
 * `GET /v1/knowledge/documents` byte for byte. The rule this file exists for
 * is that a fixture of the VIEW type says nothing about the wire, and
 * `source_type: 'md'` is a wire value that did not exist a day ago.
 *
 * `figures_pending` came from this real capture (#157). The overview and list
 * now read it, and the assertion below keeps the wire-to-view count intact.
 */
const REAL_MARKDOWN_DOCUMENT_ROW = {
  id: '6c6f6bf6-3543-4ccf-ac03-240ce7936420',
  revision: 1,
  title: 'NOTAREAL markdown capture',
  source_type: 'md',
  original_filename: 'NOTAREAL.md',
  mime_type: 'text/markdown',
  source_bytes: 109,
  status: 'draft',
  parse_error_code: null,
  created_at: '2026-09-07T16:53:07.612999Z',
  updated_at: '2026-09-07T16:53:07.612999Z',
  published_at: null,
  figures_pending: 2,
}

/**
 * Deferred so this RED still TYPECHECKS. `sourceLabel` does not exist yet, and
 * a literal `await import('@/lib/admin/knowledge')` lets tsc see the missing
 * export - which fails gate 8 at the RED commit, where the only thing that
 * should be failing is the test. The house `load()` pattern: a variable
 * specifier with `@vite-ignore` resolves at runtime, so the case fails on the
 * behaviour rather than the build.
 */
async function loadKnowledge(): Promise<{ sourceLabel: (value: string) => string }> {
  const specifier = '@/lib/admin/knowledge'
  return (await import(/* @vite-ignore */ specifier)) as unknown as {
    sourceLabel: (value: string) => string
  }
}

describe('a Markdown document against the real response', () => {
  it('carries the source type the API now sends', async () => {
    stubUpstream([REAL_MARKDOWN_DOCUMENT_ROW])
    const { readDocumentRows } = await import('@/lib/admin/knowledge.server')
    const read = await readDocumentRows(
      new Request('https://demo.example/admin/knowledge', {
        headers: { cookie: await session() },
      }),
    )
    expect(read.state).toBe('ok')
    if (read.state !== 'ok') return
    expect(read.data[0].source_type).toBe('md')
    expect(read.data[0].figures_pending).toBe(REAL_MARKDOWN_DOCUMENT_ROW.figures_pending)
  })

  it('has an English label for it, so the list never prints the enum', async () => {
    const { sourceLabel } = await loadKnowledge()
    expect(sourceLabel('md')).toBe('Markdown')
  })

  it('renders a source the tier has not been told about rather than nothing', async () => {
    /*
     * The CallEndReason lesson, on a second enum. `SOURCE_LABELS` is a
     * `Record` over the union, so an unknown value indexes to `undefined` -
     * which React renders as an EMPTY CELL. A reviewer sees a document with no
     * source and nothing to search for; falling back to the raw value keeps
     * the failure legible and greppable, which is what turns "the UI looks
     * broken" into "the API sends a type we do not label yet".
     */
    const { sourceLabel } = await loadKnowledge()
    expect(sourceLabel('epub')).toBe('epub')
  })
})
