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
