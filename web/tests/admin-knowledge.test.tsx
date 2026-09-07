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

/**
 * The document detail a reviewer can scope from (finding G7).
 *
 * G7: "native unstyled <select> beside an Astryx button; each figure is a full
 * card (value, tiny type label, yellow 'not approved' pill, sentence, grey
 * Approve) so four figures fill a screen; 'not approved' in warning yellow for
 * a DEFAULT state; no per-chunk summary ('4 figures, 0 approved'); 'All
 * documents' link pattern as G5."
 *
 * FOUR FIGURES FILLING A SCREEN IS THE REAL COST. Every figure was its own
 * Card with its own padding, so a document with a dozen extracted numbers -
 * which is a normal payment-plan PDF - became a dozen screens of scrolling to
 * approve a dozen values. Dense one-line rows put the whole decision in view,
 * and the per-chunk summary means a reviewer can tell at a glance whether a
 * section needs them at all.
 *
 * THE BADGE WEIGHTS DEPART FROM THE CARD, DELIBERATELY, and this is the one
 * judgement in F worth arguing. The card says "'pending' neutral instead of
 * warning yellow, approved in brass". The first half is right and is here. The
 * second half contradicts the badge rule PR E established and god accepted on
 * the record - brass marks WHAT NEEDS A REVIEWER, which is why `qualified` is
 * neutral on the leads list while `unreviewed` is brass. Painting an APPROVED
 * figure brass would make brass mean "done" on this screen and "needs you" on
 * the other, which is exactly the two-vocabularies problem pair 1 just fixed
 * in the lead detail. So: NOT APPROVED is brass because it is the action, and
 * SPEAKABLE is neutral because it is finished. One line to reverse if god
 * wants the card read literally.
 */
describe('the document detail a reviewer scopes from', () => {
  const CHUNK_SCOPE = 'general_knowledge' as const

  async function renderDense(figures: KnowledgeFigureView[]) {
    return renderFigures({ figures, chunkScope: CHUNK_SCOPE })
  }

  it('summarises each section before a reviewer reads a single row', async () => {
    /*
     * "4 figures, 1 approved" is the whole question a reviewer has about a
     * section they have not opened. Without it they had to count pills.
     */
    await renderDense([UNAPPROVED, SAME_VALUE_ELSEWHERE, APPROVED])
    expect(screen.getByText(/3 figures, 1 approved/i)).toBeInTheDocument()
  })

  it('counts one figure in the singular', async () => {
    await renderDense([UNAPPROVED])
    expect(screen.getByText(/^1 figure, 0 approved$/i)).toBeInTheDocument()
  })

  it('puts each figure on one dense row rather than in its own card', async () => {
    await renderDense([UNAPPROVED, APPROVED])
    const rows = screen.getAllByTestId('figure-row')
    expect(rows).toHaveLength(2)
    // The row carries the whole decision: the value, what kind it is, the
    // sentence it came from, and the control that acts on it.
    const first = rows[0]
    expect(within(first).getByText(UNAPPROVED.surface)).toBeInTheDocument()
    expect(within(first).getByText(UNAPPROVED.source_sentence)).toBeInTheDocument()
    expect(within(first).getByRole('button', { name: /approve/i })).toBeInTheDocument()
  })

  it('marks an unapproved figure in brass, because that is the one needing a reviewer', async () => {
    await renderDense([UNAPPROVED])
    const badge = screen.getByText('not approved')
    expect(badge.closest('[data-variant]')).toHaveAttribute('data-variant', 'accent')
  })

  it('leaves a speakable figure quiet, because it is finished work', async () => {
    await renderDense([APPROVED])
    const badge = screen.getByText('speakable')
    expect(badge.closest('[data-variant]')).toHaveAttribute('data-variant', 'neutral')
  })

  it('uses no warning yellow anywhere in the figure list', async () => {
    /*
     * G7's actual complaint. An unscoped section is the DEFAULT state of a
     * freshly parsed document - it is what the reviewer is here to change,
     * not a fault - and warning yellow told them something had gone wrong on
     * every figure of every new document.
     */
    /*
     * ON AN admin_only CHUNK, which is the state that matters: that is the
     * DEFAULT scope of a freshly parsed document and the only branch that
     * renders warning yellow today. My first draft of this case passed a
     * general_knowledge chunk, where the warning branch is unreachable - a
     * case that could not fail, testing the fixture rather than the code.
     */
    await renderFigures({
      figures: [UNAPPROVED, SAME_VALUE_ELSEWHERE, APPROVED],
      chunkScope: 'admin_only',
    })
    // Positive precondition: there ARE badges to check.
    const badges = document.querySelectorAll('[data-variant]')
    expect(badges.length).toBeGreaterThan(0)
    expect([...badges].map((b) => b.getAttribute('data-variant'))).not.toContain('warning')
  })

  it('styles the scope select from the theme rather than from the current colour', async () => {
    /*
     * G7: "native unstyled <select> beside an Astryx button". It was already
     * inside Astryx's Field - the label wiring was never the problem - but it
     * drew its own border from `border-current/25`, so it took the text
     * colour at whatever opacity rather than the theme's border token, and sat
     * beside themed controls looking like neither.
     */
    await renderScope({
      id: 'chunk-1',
      ordinal: 0,
      heading: 'Payment plan',
      retrieval_scope: 'admin_only',
      project_id: null,
      conflict_code: null,
      figures: [],
    } as never)
    const select = screen.getByLabelText(/scope/i)
    expect(select.className).toContain('var(--color-border)')
    expect(select.className).not.toContain('border-current')
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

/**
 * The knowledge list a reviewer can read (finding G6).
 *
 * G6's list half: "source shows raw 'PASTE'", "the Draft badge is stretched to
 * the column width", "timestamps without zone", "no counts, filters or empty
 * state". The intake half is PR F's.
 *
 * THE SOURCE IS THE ONE THAT MATTERS MOST, because it is the only cell on the
 * row whose value is an ENUM NAME rather than a word. `source_type` is
 * pdf|docx|txt|paste in the database, and the list rendered it uppercased -
 * so a pasted paragraph read as "PASTE", which is a database value on a
 * reviewer's screen and not English. "Word" for `docx` is the same argument
 * one step further: nobody outside this repository calls a Word document a
 * docx.
 *
 * THE STRETCHED BADGE HAS A CAUSE, not just a symptom. The status cell wraps
 * its badge in `flex flex-col`, and a flex column stretches its children to
 * the full cross-axis by default - so the badge grew to the column's width
 * and a two-word status became a banner. `items-start` is the fix, and these
 * cases assert the class because that is what the card allows for badge
 * sizing and what jsdom can actually see: stylex classes carry no computed
 * width here.
 */
/**
 * The file control as a CTA (human request, 2026-09-07T13:45Z: "Make choose
 * file in the knowledge screen as a CTA, currently it's just a text").
 *
 * WHAT THEY SAW. Under "Or a file", the native `<input type="file">` renders
 * as the browser's own chrome - the words "Choose File" and "No file chosen" -
 * sitting next to the themed "Add document" button. It is the one control on
 * the page that looks like nothing, because a native file input's button is
 * shadow DOM and cannot be themed cross-browser at all. CSS on the input was
 * never going to fix it; a wrapper is the fix.
 *
 * THE NATIVE INPUT STAYS IN THE DOM, visually hidden rather than replaced.
 * `knowledge-intake.tsx` explains why Astryx's FileInput was not adopted - it
 * is controlled by a `File | null` where this form resets through
 * `fileRef.current.value = ''` - and that reasoning still holds. The closure's
 * existing cases also pin the input's `accept` string and drive it with
 * `userEvent.upload`, so removing it would break the path the human reported
 * a bug on in #147.
 *
 * ONE OF THE FOUR ASSERTIONS IN THE CONTRACT IS NOT UNIT-TESTABLE, and saying
 * so is more useful than faking it. "No file chosen" is BROWSER CHROME, not
 * DOM text: jsdom never renders it, so `queryByText(/no file chosen/i)` is
 * null before this change and null after - an assertion that cannot fail in
 * either direction, which is the vacuous-negative trap. What is testable is
 * the MECHANISM that removes it: the input is visually hidden. That is pinned
 * below, and the human-visible half is verified in the browser run with a
 * before/after screenshot, which is the only place it can be.
 */
describe('choosing a file', () => {
  /** The native input, still the thing the label names. */
  function nativeInput(): HTMLInputElement {
    return screen.getByLabelText(/file/i) as HTMLInputElement
  }

  it('offers a pressable Choose file control, not the browser default', async () => {
    await renderIntake()
    const cta = screen.getByRole('button', { name: /choose file/i })
    expect(cta).toBeInTheDocument()
    // Inside the field it belongs to, so a screen reader user meets it where
    // "Or a file" led them rather than somewhere else on the form.
    expect(cta.closest('[data-file-field]')).not.toBeNull()
  })

  it('opens the picker by forwarding a click to the native input', async () => {
    /*
     * The whole trick: the CTA cannot open a file dialog itself, only a real
     * file input can. Spying on the input's own click is the honest assertion
     * - it proves the wiring without pretending jsdom can open a dialog.
     */
    await renderIntake()
    const clicked = vi.spyOn(nativeInput(), 'click').mockImplementation(() => {})
    await userEvent.click(screen.getByRole('button', { name: /choose file/i }))
    expect(clicked).toHaveBeenCalledTimes(1)
  })

  it('keeps the native input in the DOM, hidden, with its contract intact', async () => {
    /*
     * Hidden, NOT removed, and not `display:none` either - a
     * `display:none`/`hidden` input cannot be clicked programmatically in
     * every browser, which would break the CTA that now drives it. The
     * sr-only pattern keeps it in the layout at zero size.
     *
     * This is also the mechanism behind the human-visible half of the
     * request: hiding the input is what removes "Choose File" and "No file
     * chosen" from the screen.
     */
    await renderIntake()
    const input = nativeInput()
    expect(input).toBeInTheDocument()
    expect(input.className).toContain('sr-only')
    // The contract the closure's other cases depend on, unchanged.
    expect(input.accept).toBe('.pdf,.docx,.txt')
    expect(input.id).toBe('doc-file')
  })

  it('names the chosen file and its size once one is chosen', async () => {
    await renderIntake()
    // The positive precondition for the absence below: before choosing
    // anything there is no file line at all, so the presence after upload is
    // a change rather than something that was always there.
    expect(screen.queryByText(/\.pdf/i)).toBeNull()
    await userEvent.upload(
      nativeInput(),
      new File(['%PDF-1.4 payment plan'], 'Payment plan Q4.pdf', {
        type: 'application/pdf',
      }),
    )
    const chosen = await screen.findByText(/Payment plan Q4\.pdf/)
    expect(chosen).toBeInTheDocument()
    // The size in prose beside it: a reviewer who picked the wrong file
    // usually knows it from the size.
    expect(chosen.textContent).toMatch(/\d+\s?(B|KB|MB)/)
  })

  it('leaves Add document as the only primary action', async () => {
    /*
     * Two primaries on one form is two things claiming to be the next step.
     * The CTA is secondary: it prepares the submission, it does not make it.
     */
    await renderIntake()
    const cta = screen.getByRole('button', { name: /choose file/i })
    const submit = screen.getByRole('button', { name: /add document/i })
    expect(submit).toHaveAttribute('data-variant', 'primary')
    expect(cta.getAttribute('data-variant')).not.toBe('primary')
  })
})

/**
 * The intake as a panel, with the list first (finding G6's intake half).
 *
 * G6: "the intake form dominates the top (huge textarea, full-width black 'Add
 * document' bar) pushing the list down". A reviewer opens /admin/knowledge to
 * READ the library far more often than to add to it, and the page opened with
 * a five-row textarea and a full-width submit before showing a single
 * document. The list goes first; adding is a panel you open.
 *
 * COLLAPSED IS NOT HIDDEN. The trigger reports `aria-expanded`, so a screen
 * reader user knows the form exists and whether it is open - which a div that
 * simply appears would not tell them. And the panel is UNMOUNTED when closed
 * rather than hidden with CSS: a form still in the DOM keeps its fields in the
 * tab order and its inputs reachable by label, so `getByLabelText(/paste/i)`
 * would find a control nobody can see.
 *
 * E'S "Choose file" CTA STAYS AS IT IS. This pair moves the form; it does not
 * touch the control inside it, and every case from that pair still runs
 * against the opened panel.
 */
describe('the intake panel', () => {
  async function renderPanel() {
    const { KnowledgeIntake } = (await load(
      '@/components/admin/knowledge-intake',
    )) as unknown as { KnowledgeIntake: () => ReactElement }
    return render(<KnowledgeIntake />)
  }

  it('starts closed, with a trigger that says so', async () => {
    await renderPanel()
    const trigger = screen.getByRole('button', { name: /add document/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('keeps the form out of the DOM until it is opened', async () => {
    /*
     * Unmounted, not hidden: a form still in the DOM keeps its fields in the
     * tab order and findable by label, so a keyboard user would tab into
     * controls that are not on screen.
     */
    await renderPanel()
    expect(screen.queryByLabelText(/paste/i)).toBeNull()
    expect(screen.queryByLabelText(/^title/i)).toBeNull()
  })

  it('opens the form and says it is open', async () => {
    await renderPanel()
    const trigger = screen.getByRole('button', { name: /add document/i })
    await userEvent.click(trigger)
    expect(
      screen.getByRole('button', { name: /add document/i }),
    ).toHaveAttribute('aria-expanded', 'true')
    // The precondition for every intake case that follows: the form is there.
    expect(screen.getByLabelText(/paste/i)).toBeInTheDocument()
  })

  it('closes again, so the list is one press away', async () => {
    await renderPanel()
    const open = screen.getByRole('button', { name: /add document/i })
    await userEvent.click(open)
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    expect(screen.queryByLabelText(/paste/i)).toBeNull()
  })

  it('wraps the file input in a drop zone that names what it takes', async () => {
    /*
     * A styled label around the native input, so the whole area is a drop
     * target and a click target rather than a button beside a filename. The
     * accepted formats stay the FIELD's description - one hint, not two - so
     * this asserts the zone exists and is associated, not that the copy moved.
     */
    await renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))
    const zone = screen.getByTestId('drop-zone')
    expect(zone.tagName.toLowerCase()).toBe('label')
    expect(zone).toHaveAttribute('for', 'doc-file')
    // E's CTA is still inside it, unchanged.
    expect(within(zone).getByRole('button', { name: /choose file/i })).toBeInTheDocument()
  })
})

describe('the knowledge list a reviewer reads', () => {
  const SPELLED: DocumentRow[] = [
    {
      id: 'doc-paste',
      revision: 1,
      title: 'A pasted note',
      source_type: 'paste',
      status: 'draft',
      parse_error_code: null,
      created_at: '2026-09-03T09:00:00Z',
      published_at: null,
    },
    {
      id: 'doc-docx',
      revision: 2,
      title: 'A Word file',
      source_type: 'docx',
      status: 'published',
      parse_error_code: null,
      created_at: '2026-09-02T09:00:00Z',
      published_at: '2026-09-02T10:00:00Z',
    },
    {
      id: 'doc-txt',
      revision: 1,
      title: 'A text file',
      source_type: 'txt',
      status: 'draft',
      parse_error_code: null,
      created_at: '2026-09-01T09:00:00Z',
      published_at: null,
    },
    {
      id: 'doc-pdf',
      revision: 1,
      title: 'A PDF',
      source_type: 'pdf',
      status: 'draft',
      parse_error_code: null,
      created_at: '2026-08-31T09:00:00Z',
      published_at: null,
    },
  ]

  it('spells the source in English rather than showing the enum', async () => {
    await renderDocuments(SPELLED)
    for (const spelled of ['Pasted', 'Word', 'Text', 'PDF']) {
      expect(screen.getByText(spelled), spelled).toBeInTheDocument()
    }
    // The raw values are gone, not merely joined by the spelled ones.
    expect(screen.queryByText('PASTE')).toBeNull()
    expect(screen.queryByText('DOCX')).toBeNull()
    expect(screen.queryByText('TXT')).toBeNull()
  })

  it('keeps the status badge the width of its own words', async () => {
    await renderDocuments(SPELLED)
    const badge = screen.getByText('Published')
    // Positive first: the badge is inside the stack the status cell renders,
    // so the class assertion below is about that stack and not about some
    // other element that happens to match.
    const stack = badge.closest('[data-status-cell]')
    expect(stack).not.toBeNull()
    // A flex column stretches its children unless told not to, which is what
    // turned a two-word badge into a full-width banner.
    expect(stack?.className).toContain('items-start')
  })

  it('says when a document arrived without making a reviewer guess the zone', async () => {
    /*
     * G6: "timestamps without zone". `2026-09-03 09:00` on its own is
     * unreadable across a team in two places - it could be Dubai or UTC, and
     * the difference decides whether a document landed before or after a
     * call. The relative age is what a reviewer scans; the exact UTC instant
     * stays on `title` and in `dateTime`.
     */
    await renderDocuments(SPELLED)
    const when = screen.getByTitle('2026-09-03T09:00:00Z')
    expect(when.tagName.toLowerCase()).toBe('time')
    expect(when).toHaveAttribute('datetime', '2026-09-03T09:00:00Z')
  })

  it('says how many documents there are, and how many are published', async () => {
    /*
     * Both numbers, because they answer different questions: how much is in
     * the library, and how much of it the ambassador may actually draw on. A
     * library of forty drafts and one published document is a very different
     * state from forty published ones, and the list showed neither.
     */
    await renderDocuments(SPELLED)
    expect(screen.getByText(/4 documents/i)).toBeInTheDocument()
    expect(screen.getByText(/1 published/i)).toBeInTheDocument()
  })

  it('counts one document in the singular', async () => {
    await renderDocuments([SPELLED[0]])
    expect(screen.getByText(/^1 document, 0 published$/i)).toBeInTheDocument()
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
