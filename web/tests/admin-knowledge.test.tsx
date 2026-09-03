import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { DocumentRow, KnowledgeChunkView, KnowledgeFigureView } from '@/lib/admin/knowledge'

/**
 * Knowledge ingestion review (P2-S11c, task-p2-web-knowledge).
 *
 * The contract is `docs/10-`'s eight ingestion steps, `docs/02-`'s knowledge
 * shapes, and - for every name on screen - `ambassador/knowledge.py`, because a
 * UI that invents its own word for `inventory_governed` is a UI that disagrees
 * with the closure it is displaying.
 *
 * The load-bearing tests here are the negative ones. A figure is not speakable
 * until an admin approved THAT OCCURRENCE, and approving one occurrence of
 * "2 million" must not approve the other. Those are the properties that make
 * this review worth doing rather than a checkbox that means nothing.
 */

async function load(specifier: string): Promise<Record<string, never>> {
  // Variable specifier with @vite-ignore: a literal one resolves at transform
  // time and fails the FILE to load, which reports `no tests` (docs/06-).
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

async function renderFigures(props: {
  figures: KnowledgeFigureView[]
  chunkScope?: KnowledgeChunkView['retrieval_scope']
}) {
  const { FigureReview } = (await load('@/components/admin/figure-review')) as unknown as {
    FigureReview: (p: {
      documentId: string
      figures: KnowledgeFigureView[]
      chunkScope: KnowledgeChunkView['retrieval_scope']
    }) => ReactElement
  }
  return render(
    <FigureReview
      documentId="doc-1"
      figures={props.figures}
      chunkScope={props.chunkScope ?? 'general_knowledge'}
    />,
  )
}

async function renderScope(chunk: KnowledgeChunkView) {
  const { ChunkScope } = (await load('@/components/admin/chunk-scope')) as unknown as {
    ChunkScope: (p: { chunk: KnowledgeChunkView; projectIds: string[] }) => ReactElement
  }
  return render(<ChunkScope chunk={chunk} projectIds={['binghatti-skyrise', 'binghatti-circle']} />)
}

async function renderDocuments(rows: DocumentRow[]) {
  const { DocumentList } = (await load('@/components/admin/document-list')) as unknown as {
    DocumentList: (p: { rows: DocumentRow[] }) => ReactElement
  }
  return render(<DocumentList rows={rows} />)
}

async function renderIntake() {
  const { KnowledgeIntake } = (await load('@/components/admin/knowledge-intake')) as unknown as {
    KnowledgeIntake: () => ReactElement
  }
  return render(<KnowledgeIntake />)
}

const UNAPPROVED: KnowledgeFigureView = {
  id: 'fig-1',
  value: '2000000',
  kind: 'amount',
  currency: 'AED',
  unit: null,
  surface: 'AED 2,000,000',
  source_sentence: 'Two bedroom residences start at AED 2,000,000 in the current release.',
  page: 4,
  active_approval_id: null,
}

/** The same VALUE, a different occurrence. Approval must not carry across. */
const SAME_VALUE_ELSEWHERE: KnowledgeFigureView = {
  ...UNAPPROVED,
  id: 'fig-2',
  source_sentence: 'A two bedroom at the sister tower is also AED 2,000,000.',
  page: 9,
}

const APPROVED: KnowledgeFigureView = { ...UNAPPROVED, id: 'fig-3', active_approval_id: 'appr-1' }

function stubFetch(status = 201, body: unknown = { ok: true }) {
  const sent: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push({
        url: String(input),
        method: String(init?.method ?? 'GET'),
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body,
      })
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch,
  )
  return sent
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the extracted figure list', () => {
  it('shows the value, its unit, the sentence it came from and the page', async () => {
    await renderFigures({ figures: [UNAPPROVED] })
    expect(screen.getByText('AED 2,000,000')).toBeInTheDocument()
    // docs/10-: approving a value without its sentence and page is not review.
    expect(screen.getByText(/Two bedroom residences start at/)).toBeInTheDocument()
    expect(screen.getByText(/page 4/i)).toBeInTheDocument()
  })

  it('does not present an unapproved occurrence as speakable', async () => {
    await renderFigures({ figures: [UNAPPROVED] })
    const row = screen.getByText('AED 2,000,000').closest('li') as HTMLElement
    expect(within(row).getByText(/not approved/i)).toBeInTheDocument()
    expect(within(row).queryByText(/^speakable$/i)).not.toBeInTheDocument()
  })

  it('presents an approved occurrence as speakable', async () => {
    await renderFigures({ figures: [APPROVED] })
    const row = screen.getByText('AED 2,000,000').closest('li') as HTMLElement
    expect(within(row).getByText(/speakable/i)).toBeInTheDocument()
  })

  it('has no way to approve everything at once', async () => {
    await renderFigures({ figures: [UNAPPROVED, SAME_VALUE_ELSEWHERE] })
    // Approval is per occurrence (docs/10- step 6). A bulk control is how a
    // reviewer approves a sentence they never read.
    expect(screen.queryByRole('button', { name: /approve all|approve everything/i })).toBeNull()
    expect(screen.getAllByRole('button', { name: /^approve$/i })).toHaveLength(2)
  })

  it('approves one occurrence without touching another of the same value', async () => {
    const sent = stubFetch()
    await renderFigures({ figures: [UNAPPROVED, SAME_VALUE_ELSEWHERE] })
    await userEvent.click(screen.getAllByRole('button', { name: /^approve$/i })[0])
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/api/admin/knowledge/figures/fig-1/reviews')
    expect(sent[0].body).toMatchObject({ action: 'approved' })
  })

  it('revokes an approval with the action the contract names', async () => {
    const sent = stubFetch()
    await renderFigures({ figures: [APPROVED] })
    await userEvent.click(screen.getByRole('button', { name: /revoke/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({ action: 'revoked' })
  })

  it('never calls an approved figure speakable inside an inventory_governed chunk', async () => {
    await renderFigures({ figures: [APPROVED], chunkScope: 'inventory_governed' })
    const row = screen.getByText('AED 2,000,000').closest('li') as HTMLElement
    // docs/10- step 6: approving a figure never turns inventory_governed
    // material into prompt material. The tick is real; the consequence is not.
    expect(within(row).getByText(/inventory governs this/i)).toBeInTheDocument()
    expect(within(row).queryByText(/^speakable$/i)).not.toBeInTheDocument()
  })
})

describe('chunk scope', () => {
  const base: KnowledgeChunkView = {
    id: 'chunk-1',
    ordinal: 0,
    heading: 'Payment plans',
    body: 'The plan is described here.',
    retrieval_scope: 'admin_only',
    project_id: null,
    conflict_code: null,
    page_start: 1,
    page_end: 1,
    figures: [],
  }

  it('offers exactly the four scope names the closure uses', async () => {
    await renderScope(base)
    const select = screen.getByLabelText(/scope/i) as HTMLSelectElement
    expect([...select.options].map((option) => option.value)).toEqual([
      'admin_only',
      'general_knowledge',
      'project_knowledge',
      'inventory_governed',
    ])
  })

  it('requires a project before project_knowledge can be saved', async () => {
    await renderScope(base)
    await userEvent.selectOptions(screen.getByLabelText(/scope/i), 'project_knowledge')
    expect(screen.getByRole('button', { name: /save scope/i })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText(/project/i), 'binghatti-skyrise')
    expect(screen.getByRole('button', { name: /save scope/i })).toBeEnabled()
  })

  it('says what unknown_project means and that it stays closed', async () => {
    await renderScope({ ...base, conflict_code: 'unknown_project' })
    expect(screen.getByText(/unknown project/i)).toBeInTheDocument()
    expect(screen.getByText(/not publishable|stays admin-only|cannot be published/i)).toBeInTheDocument()
  })

  it('says a conflict with inventory overrides the reviewer and stays admin-only', async () => {
    await renderScope({ ...base, conflict_code: 'conflicts_with_inventory' })
    expect(screen.getByText(/conflicts with inventory/i)).toBeInTheDocument()
    expect(screen.getByText(/stays admin-only|remains admin-only/i)).toBeInTheDocument()
  })
})

describe('the document list', () => {
  const rows: DocumentRow[] = [
    {
      id: 'doc-1',
      revision: 1,
      title: 'Skyrise brochure',
      source_type: 'pdf',
      status: 'draft',
      parse_error_code: null,
      created_at: '2026-09-03T09:00:00Z',
      published_at: null,
    },
    {
      id: 'doc-2',
      revision: 1,
      title: 'Scanned flyer',
      source_type: 'pdf',
      status: 'failed',
      parse_error_code: 'no_extractable_text',
      created_at: '2026-09-03T08:00:00Z',
      published_at: null,
    },
  ]

  it('shows each document with its status', async () => {
    await renderDocuments(rows)
    expect(screen.getByText('Skyrise brochure')).toBeInTheDocument()
    expect(screen.getByText(/draft/i)).toBeInTheDocument()
  })

  it('explains a scanned PDF rather than showing a bare failure', async () => {
    await renderDocuments(rows)
    const row = screen.getByText('Scanned flyer').closest('tr') as HTMLElement
    // docs/10- step 2: it tells the admin that scans need OCR, which is deferred.
    expect(within(row).getByText(/no extractable text/i)).toBeInTheDocument()
    expect(within(row).getByText(/ocr/i)).toBeInTheDocument()
  })
})

describe('intake', () => {
  it('posts pasted text as its own source type', async () => {
    const sent = stubFetch(201, { id: 'doc-9' })
    await renderIntake()
    await userEvent.type(screen.getByLabelText(/paste/i), 'A paragraph about the payment plan.')
    await userEvent.type(screen.getByLabelText(/title/i), 'Payment plan note')
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/api/admin/knowledge/documents')
    expect(sent[0].body).toMatchObject({ source_type: 'paste', title: 'Payment plan note' })
  })

  it('refuses an over-sized file in the browser and sends nothing', async () => {
    const sent = stubFetch()
    await renderIntake()
    const input = screen.getByLabelText(/file/i) as HTMLInputElement
    const huge = new File([new Uint8Array(12 * 1024 * 1024)], 'big.pdf', {
      type: 'application/pdf',
    })
    await userEvent.upload(input, huge)
    // The API caps this too - that is the real gate. Refusing here saves a
    // reviewer uploading 12MB to be told no, and says the limit out loud.
    expect(await screen.findByText(/too large|limit/i)).toBeInTheDocument()
    expect(sent).toHaveLength(0)
  })

  it('names the formats the API will accept and no others', async () => {
    await renderIntake()
    const input = screen.getByLabelText(/file/i) as HTMLInputElement
    expect(input.accept).toBe('.pdf,.docx,.txt')
  })
})
