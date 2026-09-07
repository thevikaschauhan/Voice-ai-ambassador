import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { LeadDetailRecord, LeadSummaryRow } from '@/lib/admin/leads'

/**
 * Imported inside each case rather than at the top, so this file COLLECTS
 * before the components exist. A RED commit whose test file fails to load
 * reports "no tests" and the gate has no case failures to count against the
 * new cases; dynamic imports make each case fail on its own.
 */
/**
 * The specifier is a variable and the import carries `@vite-ignore`, because
 * Vite resolves a LITERAL dynamic import at transform time too - which fails
 * the whole file to load rather than failing each case, and a file that does
 * not load reports "no tests".
 */
async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

async function renderList(rows: LeadSummaryRow[]) {
  const { LeadList } = (await load('@/components/admin/lead-list')) as unknown as {
    LeadList: (props: { rows: LeadSummaryRow[] }) => ReactElement
  }
  return render(<LeadList rows={rows} />)
}

async function renderDetail(lead: LeadDetailRecord) {
  const { LeadDetail } = (await load('@/components/admin/lead-detail')) as unknown as {
    LeadDetail: (props: { lead: LeadDetailRecord }) => ReactElement
  }
  return render(<LeadDetail lead={lead} />)
}

/**
 * The lead list, the detail and the decision (P2-S11, task-p2-web-leads).
 *
 * The contract is `docs/10-admin.md` and `docs/02-`'s Phase 2 lead record. Two
 * of these tests are about what must NOT appear: the list shows operational
 * fields only, because buyer words on a list is a transcript nobody chose to
 * open, and the summary must be labelled as generated wherever it is shown,
 * because an unlabelled model sentence reads as a fact somebody checked.
 */

const ROWS: LeadSummaryRow[] = [
  {
    id: 'lead-1',
    session_id: 'sess-1',
    created_at: '2026-09-03T09:00:00Z',
    ended_at: '2026-09-03T09:07:30Z',
    call_end_reason: 'buyer_farewell',
    ended_cleanly: true,
    language: 'en',
    status: 'unreviewed',
    score_total: 61,
    project_ids: ['binghatti-skyrise'],
    contact_present: true,
    analysis_status: 'complete',
  },
  {
    id: 'lead-2',
    session_id: 'sess-2',
    created_at: '2026-09-03T08:00:00Z',
    ended_at: '2026-09-03T08:00:20Z',
    call_end_reason: 'buyer_left',
    ended_cleanly: false,
    language: 'hi',
    status: 'rejected',
    score_total: null,
    project_ids: [],
    contact_present: false,
    analysis_status: 'failed',
  },
]

/**
 * THE VIEW TYPE, NOT THE WIRE SHAPE, and the distinction is the whole reason
 * task-web-lead-detail-score existed. `LeadDetailRecord` is what the mapper
 * produces, so a fixture built from it is correct here and says NOTHING about
 * what the admin API sends - and for a year it sent no `score` object at all,
 * a `payload` string instead of turn text, and `created_at` instead of
 * `decided_at`, while this file stayed green.
 *
 * The wire shape is pinned in `admin-real-shapes.test.ts` against a CAPTURED
 * response. If you are about to add a field to this fixture, add it there
 * first: a case that only ever sees the view type cannot tell you the mapper
 * is wrong.
 */
const DETAIL: LeadDetailRecord = {
  ...ROWS[0],
  revision: 3,
  summary: 'The buyer asked about a two bedroom and gave a budget.',
  score: {
    total: 61,
    score_version: 'v1',
    breakdown: [
      {
        signal: 'budget_stated',
        observed: true,
        raw_value: true,
        points_awarded: 15,
        max_points: 15,
        evidence_turn_indexes: [4],
      },
      {
        signal: 'contact_shared',
        observed: true,
        raw_value: true,
        points_awarded: 20,
        max_points: 20,
        evidence_turn_indexes: [9],
      },
      {
        signal: 'timeline_stated',
        observed: false,
        raw_value: false,
        points_awarded: 0,
        max_points: 10,
        evidence_turn_indexes: [],
      },
    ],
  },
  contact: { status: 'captured', name: 'A buyer', phone: '+971500000000', email: null },
  turns: [
    { turn_index: 4, speaker: 'buyer', text: 'My budget is two million.', audit_incomplete: false },
    { turn_index: 9, speaker: 'buyer', text: 'You can reach me on this number.', audit_incomplete: false },
  ],
  decisions: [
    {
      id: 'dec-1',
      sequence: 1,
      previous_status: 'unreviewed',
      new_status: 'rejected',
      reason_code: 'follow_up',
      note: 'called back later',
      decided_at: '2026-09-03T10:00:00Z',
    },
  ],
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the lead list', () => {
  it('shows the operational fields and no buyer words at all', async () => {
    await renderList(ROWS)
    expect(screen.getByText('61')).toBeInTheDocument()
    expect(screen.getByText(/binghatti-skyrise/)).toBeInTheDocument()
    // docs/10-: buyer words and contact values appear on the DETAIL only. A
    // transcript line on a list is a transcript nobody chose to open.
    expect(screen.queryByText(/My budget is two million/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\+971500000000/)).not.toBeInTheDocument()
  })

  it('says a call did not end cleanly rather than hiding it among complete ones', async () => {
    await renderList(ROWS)
    const row = screen.getByText('sess-2').closest('tr') as HTMLElement
    expect(within(row).getByText(/incomplete/i)).toBeInTheDocument()
    expect(within(row).getByText(/buyer left/i)).toBeInTheDocument()
  })

  it('shows a failed analysis as failed, not as a score of zero', async () => {
    await renderList(ROWS)
    const row = screen.getByText('sess-2').closest('tr') as HTMLElement
    expect(within(row).getByText(/analysis failed/i)).toBeInTheDocument()
    expect(within(row).queryByText('0')).not.toBeInTheDocument()
  })

  it('names the review status in the words the closure uses, not the raw enum', async () => {
    /*
     * A REAL DEFECT, not a restyle: the status cell renders `row.status`
     * straight from the API and leans on a CSS `uppercase` to make it look
     * like a label. So the text the DOM carries is "unreviewed" while the text
     * on screen is "UNREVIEWED" - a reviewer who copies the cell gets a
     * different string than the one they read, and the same vocabulary is
     * already displayed as "Unreviewed" by the overview cards on /admin. One
     * screen, two spellings of one status, neither of them the enum's.
     *
     * Asserted with exact strings for that reason: `getByText('Unreviewed')`
     * does not match "unreviewed", which is the whole point.
     */
    await renderList(ROWS)
    const unreviewed = screen.getByText('sess-1').closest('tr') as HTMLElement
    expect(within(unreviewed).getByText('Unreviewed')).toBeInTheDocument()
    const rejected = screen.getByText('sess-2').closest('tr') as HTMLElement
    expect(within(rejected).getByText('Rejected')).toBeInTheDocument()
  })

  it('says so when there are no leads yet', async () => {
    await renderList([])
    expect(screen.getByText(/no calls have been recorded/i)).toBeInTheDocument()
  })

  it('announces the empty list as a titled region, not as loose prose', async () => {
    /*
     * The sentence above it is the only thing on the page when there are no
     * leads, and today it is a bare <p>: nothing for a screen reader user
     * navigating by heading to land on, so "why is this page blank" has no
     * answer without reading the whole document. The words do not change - the
     * existing case above still passes on the same sentence - they just get a
     * heading and a description instead of one paragraph.
     */
    await renderList([])
    expect(
      screen.getByRole('heading', { name: /no calls have been recorded yet/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/including one that was cut short/i)).toBeInTheDocument()
  })
})

/**
 * The lead list a reviewer can actually work from (finding G4).
 *
 * WHAT G4 SAYS IS WRONG. Row identity is a session id - `sess-demo-a` - styled
 * as an underlined link, and only that id is clickable, so the target for the
 * one action every row has is a 90px word in a 1400px row. Three badge colour
 * systems run at once (grey Unreviewed, red Rejected, yellow incomplete) next
 * to a display-sized "61" beside a red "analysis failed" pill. The When column
 * wraps to three lines. "none named" repeats down the Projects column. There is
 * no count, no filter and no sort.
 *
 * TWO OF THESE CASES ENCODE A DEPARTURE FROM THE CARD, and it is a privacy
 * boundary rather than a preference. E asks the primary cell for "contact name
 * when captured, else the first named project, else Caller + short id". The
 * list read CANNOT supply a contact name: `list_leads` names its columns
 * precisely so it cannot leak one ("contact_name, contact_phone and
 * contact_email are absent from that list. A list page cannot leak a
 * transcript it was never handed"), docs/10 draws the same line, and E's own
 * boundaries forbid an API change. Widening that projection to put a buyer's
 * name on a list would undo what three layers of this codebase are written to
 * prevent, so the fallback starts at the PROJECT tier. `contact_present` still
 * shows whether a contact exists, which is the operational bit and not a value.
 *
 * ONE BADGE SYSTEM, and the rule is legible rather than aesthetic: neutral for
 * a state nobody has to act on, brass for one that needs a reviewer, and flag
 * red ONLY for a failure or a rejection. "Unreviewed" is the whole point of
 * this screen, so it is the brass one; "Qualified" is finished work and goes
 * quiet. Every badge keeps its WORD - a badge distinguished only by colour is
 * a badge a colour-blind reviewer cannot read - so these cases assert the
 * label AND the variant, not the colour.
 */
describe('the lead list a reviewer works from', () => {
  /** The row element for a lead, found from its own primary text. */
  function rowFor(text: string | RegExp): HTMLElement {
    const cell = screen.getByText(text)
    const row = cell.closest('tr')
    expect(row).not.toBeNull()
    return row as HTMLElement
  }

  it('makes the whole row the link target, not just the session id', async () => {
    /*
     * Astryx's Table exposes no row-level href or click prop - measured, its
     * TableProps has neither - but it does expose `transformBodyRow` through
     * the plugin pipeline, which is the supported way to put props on each
     * <tr>. So the row carries the destination and stays ONE link for a
     * screen reader rather than becoming seven.
     */
    await renderList(ROWS)
    const row = rowFor('binghatti-skyrise')
    expect(row).toHaveAttribute('data-row-href', '/admin/leads/lead-1')
    // The keyboard target stays a real link, so the row is reachable without
    // a pointer and the destination is announced.
    const link = within(row).getByRole('link')
    expect(link).toHaveAttribute('href', '/admin/leads/lead-1')
    // Exactly one: a row where every cell is a link makes a screen reader
    // read the same destination seven times.
    expect(within(row).getAllByRole('link')).toHaveLength(1)
  })

  it('names the row by its project when there is one, with the session id secondary', async () => {
    await renderList(ROWS)
    const row = rowFor('binghatti-skyrise')
    // The primary cell leads with the project, and the session id is still
    // present - a reviewer cross-referencing a log needs it - but demoted.
    expect(within(row).getByText('sess-1')).toBeInTheDocument()
    expect(within(row).getByRole('link')).toHaveAccessibleName(/binghatti-skyrise/)
  })

  it('falls back to a caller and the short id when no project was named', async () => {
    /*
     * The second tier, not the third: the contact-name tier the card asks for
     * is not reachable from this projection (see the note above). "Caller"
     * plus the short id is a human-readable handle that promises nothing the
     * record does not carry - unlike a person icon or a name it does not have.
     */
    await renderList(ROWS)
    const row = rowFor(/caller/i)
    expect(within(row).getByText('sess-2')).toBeInTheDocument()
    expect(within(row).getByRole('link')).toHaveAccessibleName(/caller lead-2/i)
  })

  it('marks the status needing a reviewer in brass and finished work in neutral', async () => {
    await renderList(ROWS)
    // Positive first: both badges are present with their words, so the
    // variant assertions below are about badges that exist.
    const unreviewed = screen.getByText('Unreviewed')
    const rejected = screen.getByText('Rejected')
    expect(unreviewed.closest('[data-variant]')).toHaveAttribute('data-variant', 'accent')
    // Flag red is reserved for a failure or a rejection.
    expect(rejected.closest('[data-variant]')).toHaveAttribute('data-variant', 'error')
  })

  it('stops using warning yellow for a state that is not a warning', async () => {
    /*
     * G4 counts "yellow incomplete" as one of the three colour systems, and
     * G7 makes the same point about "not approved". A call the buyer cut short
     * is a FACT about the call, not a problem a reviewer must fix, so it reads
     * in the neutral scale and keeps its word.
     */
    await renderList(ROWS)
    const incomplete = screen.getByText('incomplete')
    expect(incomplete.closest('[data-variant]')).toHaveAttribute('data-variant', 'neutral')
  })

  it('keeps a captured contact in the neutral scale, since it is not an action', async () => {
    await renderList(ROWS)
    const captured = screen.getByText('captured')
    expect(captured.closest('[data-variant]')).toHaveAttribute('data-variant', 'neutral')
  })

  it('says how many rows are being shown', async () => {
    await renderList(ROWS)
    expect(screen.getByText(/2 calls/i)).toBeInTheDocument()
  })

  it('counts one call in the singular', async () => {
    // A count that reads "1 calls" is the kind of detail that makes a screen
    // look unfinished, and it is one branch.
    await renderList([ROWS[0]])
    expect(screen.getByText(/^1 call$/i)).toBeInTheDocument()
  })

  it('puts when the call happened on one line, with the exact time in reach', async () => {
    /*
     * G4: "the When column wraps to three lines". A relative age is what a
     * reviewer scans by; the exact timestamp stays on the element's title and
     * in `dateTime`, so nothing machine-readable is lost.
     */
    await renderList(ROWS)
    const when = screen.getByTitle('2026-09-03T09:00:00Z')
    expect(when.tagName.toLowerCase()).toBe('time')
    expect(when).toHaveAttribute('datetime', '2026-09-03T09:00:00Z')
    // One line: the duration moves out of this column rather than stacking
    // under the date.
    expect(when.textContent).not.toMatch(/\n/)
  })

  it('shows the score as a compact numeral rather than a display heading', async () => {
    // A score is data in a column, not a page title. `display-3` made "61"
    // the largest thing on the row and G4 calls it "a huge 61".
    await renderList(ROWS)
    const score = screen.getByText('61')
    expect(score).toHaveAttribute('data-score')
    expect(score.getAttribute('data-type')).not.toMatch(/display/)
  })
})

describe('the lead detail', () => {
  it('labels the summary as model-generated wherever it appears', async () => {
    await renderDetail(DETAIL)
    expect(screen.getByText(/two bedroom and gave a budget/)).toBeInTheDocument()
    // An unlabelled model sentence reads as a fact somebody checked.
    expect(screen.getByText(/generated/i)).toBeInTheDocument()
  })

  it('shows the score with its evidence, not just the number', async () => {
    await renderDetail(DETAIL)
    expect(screen.getByText('61')).toBeInTheDocument()
    const budget = screen.getByText(/budget stated/i).closest('li') as HTMLElement
    expect(within(budget).getByText('15')).toBeInTheDocument()
    // The evidence turn is what makes a score reviewable rather than asserted.
    expect(within(budget).getByText(/turn 4/i)).toBeInTheDocument()
  })

  it('shows a signal that scored nothing, so the total is legible', async () => {
    await renderDetail(DETAIL)
    const timeline = screen.getByText(/timeline stated/i).closest('li') as HTMLElement
    expect(within(timeline).getByText(/not observed/i)).toBeInTheDocument()
  })

  it('shows the immutable decision history', async () => {
    await renderDetail(DETAIL)
    // Scoped to the history, because "Follow up" is also a reason the form
    // offers - both appearing is correct, so the assertion has to say which
    // one it means.
    const history = screen.getByText(/called back later/).closest('li') as HTMLElement
    expect(within(history).getByText(/rejected/i)).toBeInTheDocument()
    expect(within(history).getByText(/follow up/i)).toBeInTheDocument()
    // Append-only in the database (ADR-020), so nothing here is editable.
    expect(history.querySelectorAll('input, textarea, select, button')).toHaveLength(0)
  })

  it('leaves the page heading to the shell instead of adding a second one', async () => {
    /*
     * A DEFECT THIS PR INTRODUCED, found by reading the detail page rather
     * than by the browser run - which only visited /admin, /admin/leads and
     * /admin/knowledge, never a detail route. AdminAppShell now renders the
     * page h1 so that no page can forget one; this component still renders its
     * own h1 for the session id, so /admin/leads/<id> ships TWO level-one
     * headings and a screen reader user navigating by h1 gets two page titles,
     * neither of which is the page.
     *
     * The shell owns the h1, so the component must not have one. The session
     * id is not lost: the shell takes it as the page heading and the top bar
     * keeps the short label.
     */
    await renderDetail(DETAIL)
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
  })

  it('says which decision is chosen, not just which one is tinted', async () => {
    /*
     * Qualify and Reject are two buttons whose selected state is carried
     * ENTIRELY by a border and a text colour. Nothing in the accessibility
     * tree changes when one is pressed, so a screen reader user cannot tell
     * which decision they are about to save - and neither can a sighted
     * reviewer who cannot separate brass from ink. The choice is exclusive, so
     * pressing one must also un-announce the other.
     */
    await renderDetail(DETAIL)
    const qualify = screen.getByRole('button', { name: /qualify/i })
    const reject = screen.getByRole('button', { name: /reject/i })
    expect(qualify).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(qualify)
    expect(qualify).toHaveAttribute('aria-pressed', 'true')
    expect(reject).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(reject)
    expect(qualify).toHaveAttribute('aria-pressed', 'false')
    expect(reject).toHaveAttribute('aria-pressed', 'true')
  })

  it('names the statuses it shows in the words the closure uses', async () => {
    /*
     * The same raw-enum-under-a-CSS-uppercase as the two lists, twice on this
     * page: the lead's own status in the header, and each recorded decision's
     * status in the history.
     */
    await renderDetail(DETAIL)
    expect(screen.getByText('Unreviewed')).toBeInTheDocument()
    const history = screen.getByText(/called back later/).closest('li') as HTMLElement
    expect(within(history).getByText('Rejected')).toBeInTheDocument()
  })
})

describe('qualifying and rejecting', () => {
  function stubDecision(status: number, body: unknown) {
    const sent: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        sent.push({ url: String(input), body: JSON.parse(String(init?.body ?? 'null')) })
        return new Response(JSON.stringify(body), {
          status,
          headers: { 'content-type': 'application/json' },
        })
      }) as typeof fetch,
    )
    return sent
  }

  it('sends the decision with the revision it was shown', async () => {
    const sent = stubDecision(201, { revision: 4, status: 'qualified' })
    await renderDetail(DETAIL)
    await userEvent.click(screen.getByRole('button', { name: /qualify/i }))
    await userEvent.type(screen.getByLabelText(/note/i), 'looks ready')
    await userEvent.click(screen.getByRole('button', { name: /save decision/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/api/admin/leads/lead-1/decisions')
    // Optimistic concurrency: the revision the reviewer was LOOKING at, so a
    // decision made against stale data is refused rather than applied.
    expect(sent[0].body).toMatchObject({
      new_status: 'qualified',
      reason_code: expect.any(String),
      note: 'looks ready',
      // Corrected, not deleted: this case asserted `revision` in #109, which
      // was this tier's guess at a field the API had not shipped yet. The
      // guess was wrong, and a test that encodes a wrong contract is worse
      // than no test - it makes the drift look verified.
      expected_lead_revision: 3,
    })
  })

  it('names the revision field the API actually validates', async () => {
    // Drift found against toby's merged admin_api.py: DecisionRequest declares
    // `expected_lead_revision` (docs/02- names it that too), and this component
    // was sending `revision`. The proxy forwards bodies verbatim, so the route
    // could not catch it - every decision would have 422'd, which reads as a
    // haunted failure rather than a field name.
    const sent = stubDecision(201, { revision: 4 })
    await renderDetail(DETAIL)
    await userEvent.click(screen.getByRole('button', { name: /qualify/i }))
    await userEvent.click(screen.getByRole('button', { name: /save decision/i }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({ expected_lead_revision: 3 })
    expect(sent[0].body).not.toHaveProperty('revision')
  })

  it('tells the reviewer to reload when the revision has moved under them', async () => {
    stubDecision(409, { error: 'revision has moved' })
    await renderDetail(DETAIL)
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    await userEvent.click(screen.getByRole('button', { name: /save decision/i }))
    // A 409 is not a failure to hide: somebody else decided first, and
    // silently retrying would overwrite their decision.
    expect(await screen.findByText(/somebody else|reload|moved/i)).toBeInTheDocument()
  })

  /**
   * REWRITTEN, not deleted (task-web-silent-disabled-family). The claim it
   * made is the one that matters and is kept verbatim - nothing is posted
   * until a decision is chosen - but it asserted that claim AS A DISABLED
   * BUTTON, and a disabled button is how an admin finds out nothing. Qualify
   * or reject is a choice they can make, so the press asks for it.
   */
  it('names the missing choice instead of disabling the save, and sends nothing', async () => {
    const sent = stubDecision(201, {})
    await renderDetail(DETAIL)

    const save = screen.getByRole('button', { name: /save decision/i })
    expect(save).toBeEnabled()
    await userEvent.click(save)
    // FIXTURE STRENGTHENING, riding with this RED for the same reason 671a1ef's
    // did: the GREEN puts an Astryx Button on this form, and Astryx's Button
    // renders its OWN role=status live region for its "Loading" announcement -
    // so a bare `findByRole('status')` goes ambiguous the moment it lands. The
    // claim gets stronger rather than weaker: THIS sentence is the announced
    // one, where the role query only said some live region held the words.
    const status = (await screen.findByText(/choose qualify or reject/i)).closest(
      '[role="status"]',
    )
    expect(status).not.toBeNull()
    // The original claim, kept: no decision reaches the lead without a choice.
    expect(sent).toHaveLength(0)
  })
})
