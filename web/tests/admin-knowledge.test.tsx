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
    // `/^approve /` rather than the `/^approve$/` this case used before: each
    // control now names the occurrence it acts on, so the name is "Approve
    // <value>, page N" and an anchored exact match finds nothing. The claim is
    // the same one and the guard above it is untouched - no bulk control, and
    // exactly one per-occurrence button per figure.
    expect(screen.getAllByRole('button', { name: /^approve /i })).toHaveLength(2)
  })

  it('approves one occurrence without touching another of the same value', async () => {
    const sent = stubFetch()
    await renderFigures({ figures: [UNAPPROVED, SAME_VALUE_ELSEWHERE] })
    // Picked BY ITS NAME rather than by taking [0] out of a list of identical
    // buttons. That is the point of the change under this commit: choosing the
    // occurrence by the page it is on is what a reviewer does, where indexing
    // a NodeList is something only a test could do. fig-1 is the page-4 one.
    await userEvent.click(screen.getByRole('button', { name: /^approve .*page 4$/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/api/admin/knowledge/figures/fig-1/reviews')
    expect(sent[0].body).toMatchObject({ action: 'approved' })
  })

  it('tells two occurrences of one value apart by their buttons alone', async () => {
    /*
     * THE DEFECT THIS PAGE CAN LEAST AFFORD. Approval is per-occurrence -
     * approving "AED 2,000,000" on page 4 must not make the same words on page
     * 9 speakable - and these two fixtures are exactly that pair: same value,
     * same surface, different sentence and page. On screen they produce two
     * buttons whose accessible name is the single word "Approve".
     *
     * So a screen reader user tabbing this list hears "Approve, Approve" and
     * has nothing to choose between them, and the consequence is not cosmetic:
     * the wrong press makes a figure speakable in a context nobody reviewed.
     * The existing cases avoid the ambiguity by scoping to the row's sentence,
     * which is a thing a test can do and a person cannot.
     *
     * Each control must name the occurrence it acts on. The visible word stays
     * "Approve"; what changes is the name in the accessibility tree.
     */
    await renderFigures({ figures: [UNAPPROVED, SAME_VALUE_ELSEWHERE] })
    const buttons = screen.getAllByRole('button', { name: /approve/i })
    expect(buttons).toHaveLength(2)
    const names = buttons.map((button) => button.getAttribute('aria-label') ?? button.textContent)
    expect(new Set(names).size).toBe(2)
    // Named by the fact that separates them, not by an index a reviewer
    // cannot see: one is on page 4 and the other on page 9.
    expect(names.some((name) => name?.includes('4'))).toBe(true)
    expect(names.some((name) => name?.includes('9'))).toBe(true)
  })

  it('revokes an approval with the action the contract names', async () => {
    const sent = stubFetch()
    await renderFigures({ figures: [APPROVED] })
    await userEvent.click(screen.getByRole('button', { name: /revoke/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({ action: 'revoked' })
  })

  it('says an admin_only chunk is admin-only, not that inventory governs it', async () => {
    // admin_only is the DEFAULT, so this is the commonest case - and the two
    // closed reasons are different things to tell a reviewer: one is permanent
    // and one is an action they can take. Naming the wrong cause points at the
    // wrong fix. Found by rendering the real page, not by a unit test.
    await renderFigures({ figures: [UNAPPROVED], chunkScope: 'admin_only' })
    const row = screen.getByText('AED 2,000,000').closest('li') as HTMLElement
    expect(within(row).queryByText(/inventory governs/i)).not.toBeInTheDocument()
    expect(within(row).getByText(/not approved/i)).toBeInTheDocument()
  })

  it('says an approved figure in an admin_only chunk is still not reachable', async () => {
    await renderFigures({ figures: [APPROVED], chunkScope: 'admin_only' })
    const row = screen.getByText('AED 2,000,000').closest('li') as HTMLElement
    expect(within(row).getByText(/admin-only/i)).toBeInTheDocument()
    expect(within(row).queryByText(/^speakable$/i)).not.toBeInTheDocument()
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

  /**
   * REWRITTEN, not deleted (task-web-silent-disabled-family). The claim is
   * unchanged - project_knowledge is not saveable without a project - but the
   * old version asserted it as a DISABLED button, and a disabled button is
   * how the reviewer finds out nothing. The rule now: never disabled for a
   * missing input; pressing it names the input.
   */
  it('names the missing project instead of disabling the save', async () => {
    const sent = stubFetch()
    await renderScope(base)
    await userEvent.selectOptions(screen.getByLabelText(/scope/i), 'project_knowledge')

    const save = screen.getByRole('button', { name: /save scope/i })
    expect(save).toBeEnabled()
    await userEvent.click(save)
    // FIXTURE STRENGTHENING, riding with this RED as 671a1ef's did: the GREEN
    // puts an Astryx Button on this form, and Astryx's Button renders its OWN
    // role=status live region for its "Loading" announcement - so a bare
    // `findByRole('status')` goes ambiguous the moment it lands. Astryx's
    // EmptyState carries one too, which is a second source on this page.
    //
    // THE FULL SENTENCE, not /choose a project/: the project selector's empty
    // option reads "Choose a project", so the short pattern matches two
    // elements once the query goes by text instead of by role. The claim ends
    // up stronger either way - THIS sentence is the announced one.
    const status = (await screen.findByText(/choose a project for this scope/i)).closest(
      '[role="status"]',
    )
    expect(status).not.toBeNull()
    // The claim the old test made, kept: nothing is saved without a project.
    expect(sent).toHaveLength(0)

    await userEvent.selectOptions(screen.getByLabelText(/project/i), 'binghatti-skyrise')
    await userEvent.click(save)
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({
      action: 'project_knowledge',
      project_id: 'binghatti-skyrise',
    })
  })

  it('stays disabled when the SERVER has already refused, which is not a missing input', async () => {
    // #113's deliberate half, and the one exception to the rule: an
    // `unknown_project` closure means the server will not publish this chunk
    // whatever the reviewer picks, so there is no input that would make the
    // press succeed and nothing for a message to ask for.
    await renderScope({ ...base, conflict_code: 'unknown_project' })
    expect(screen.getByRole('button', { name: /save scope/i })).toBeDisabled()
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

  it('names every status in the words a reader uses, not the raw enum', async () => {
    /*
     * The same defect as the lead list's status cell and the same cause: the
     * status is rendered straight from the API, and the difference between a
     * draft, a published document and a failed one is carried by a colour
     * class. `DocumentStatus` has FIVE values and the colour logic branches on
     * two of them, so parsing, draft and archived are all styled alike - a
     * document still being parsed looks exactly like one ready to publish.
     *
     * All five asserted together, because the point is that the vocabulary is
     * covered rather than that one word was fixed.
     */
    const all: DocumentRow[] = (
      ['parsing', 'draft', 'published', 'failed', 'archived'] as const
    ).map((status, index) => ({
      ...rows[0],
      id: `doc-${status}`,
      title: `Document ${index}`,
      status,
      parse_error_code: null,
    }))
    await renderDocuments(all)
    for (const word of ['Parsing', 'Draft', 'Published', 'Failed', 'Archived']) {
      expect(screen.getByText(word)).toBeInTheDocument()
    }
  })

  it('announces an empty library as a titled region, not as loose prose', async () => {
    /*
     * As on the lead list: when there is nothing to show, this sentence is the
     * whole page, and a bare <p> gives a screen reader user navigating by
     * heading nothing to land on. The words are unchanged.
     */
    await renderDocuments([])
    expect(screen.getByRole('heading', { name: /no documents yet/i })).toBeInTheDocument()
    expect(screen.getByText(/paste a paragraph or upload a pdf/i)).toBeInTheDocument()
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

  /**
   * The human's report, 2026-09-07: "I'm not able to upload anything and
   * manual copy paste is not working because there's no CTA to save it".
   *
   * Reproduced in a real browser before any of this was written: with a
   * paragraph pasted and no Title, the submit control was disabled at
   * opacity 0.4, the role=status region was EMPTY, and clicking it made no
   * network request at all. Same for a chosen PDF. So the reviewer's report
   * was exactly right - there was no control that would save anything, and
   * nothing on screen said why.
   *
   * A DISABLED CONTROL IS NOT A MESSAGE. It removes the only thing a reviewer
   * can press to find out what is wrong, which is the opposite of what a form
   * that wants a field should do. These four cases are the contract instead:
   * the CTA is always pressable, pressing it says what is missing, and a title
   * is never what is missing because we can always derive one.
   */
  it('offers a pressable control once there is something to save, with no title', async () => {
    await renderIntake()
    await userEvent.type(screen.getByLabelText(/paste/i), 'A paragraph about the payment plan.')
    expect(screen.getByRole('button', { name: /add document/i })).toBeEnabled()
  })

  it('says what is missing when it is pressed with nothing filled in', async () => {
    const sent = stubFetch()
    await renderIntake()
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    // The reason goes in the region that already exists for problems, so a
    // screen reader announces it rather than a reviewer hunting for a colour.
    // FIXTURE STRENGTHENING, riding with this RED as 671a1ef's did: the GREEN
    // puts an Astryx Button on this form and Astryx's Button renders its OWN
    // role=status live region, so a bare `findByRole('status')` goes ambiguous
    // the moment it lands. Stronger, not looser: THIS sentence is the
    // announced one.
    const status = (await screen.findByText(/paste text or choose a file/i)).closest(
      '[role="status"]',
    )
    expect(status).not.toBeNull()
    expect(sent).toHaveLength(0)
  })

  it('tells the file field itself what it accepts and what will fail', async () => {
    /*
     * The most important sentence on this form is in a sibling paragraph:
     * PDF/DOCX/TXT, a size cap, and the one that saves a wasted upload - a
     * scanned PDF has no extractable text and WILL fail, because OCR is
     * deferred. Nothing associates it with the input, so a screen reader user
     * focused on the file field hears "Or a file" and gets none of it. They
     * find out by uploading a scan and reading the failure afterwards.
     *
     * Asserted as the field's accessible DESCRIPTION rather than as text
     * somewhere on the page - the words are already on the page, and being on
     * the page is exactly what is not enough.
     */
    await renderIntake()
    const input = screen.getByLabelText(/file/i)
    expect(input).toHaveAccessibleDescription(/pdf, docx or txt/i)
    expect(input).toHaveAccessibleDescription(/ocr is deferred/i)
  })

  it('titles an upload after its file when the reviewer gave no title', async () => {
    const sent = stubFetch(201, { id: 'doc-9' })
    await renderIntake()
    const input = screen.getByLabelText(/file/i) as HTMLInputElement
    await userEvent.upload(input, new File(['%PDF-1.4 payment plan'], 'Payment plan Q4.pdf', {
      type: 'application/pdf',
    }))
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/api/admin/knowledge/documents/upload')
    // Extension stripped: the reviewer named the document when they named the
    // file, and ".pdf" is not part of that name.
    expect((sent[0].body as FormData).get('title')).toBe('Payment plan Q4')
  })

  it('titles a paste after its first line when the reviewer gave no title', async () => {
    const sent = stubFetch(201, { id: 'doc-9' })
    await renderIntake()
    await userEvent.type(
      screen.getByLabelText(/paste/i),
      'Aquarise payment plan{Enter}The booking amount is 20 per cent.',
    )
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({
      source_type: 'paste',
      title: 'Aquarise payment plan',
    })
  })
})
