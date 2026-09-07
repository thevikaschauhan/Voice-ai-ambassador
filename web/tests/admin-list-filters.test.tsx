// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { LeadSummaryRow } from '@/lib/admin/leads'

/**
 * Filtering and sorting the lead list (finding G4: "no filters, search, sort,
 * count or empty/loading state").
 *
 * THE SPLIT BETWEEN THE TWO IS DELIBERATE and it is the card's own: the STATUS
 * FILTER goes to the API, because `list_leads` already takes `status`,
 * `language` and `project_id` and filtering server-side is the difference
 * between a page that works at 60k leads and one that downloads them. SORT is
 * CLIENT-SIDE, because it reorders rows already on screen and a round trip to
 * reorder fifty rows a reviewer is looking at is a round trip for nothing.
 *
 * That split is why the chips are LINKS and the sort is state. A filter is a
 * different URL - bookmarkable, shareable, survives a reload, works with the
 * middle mouse button - and a sort is a view of one URL's data.
 *
 * WHAT THE FORWARDING CASES ARE REALLY FOR. `readLeadRows` passes the page's
 * query string to the upstream URL, and the page had never given it one - the
 * Request it built carried a hardcoded path with no search at all, so the
 * filter plumbing in the lib was dead code the whole time. Sending the string
 * on VERBATIM would be the easy fix and the wrong one: the page's own UI
 * params have no business upstream, and FastAPI's tolerance for unknown query
 * params is a courtesy, not a contract - the day it tightens, a `?sort=score`
 * in the URL bar becomes a 422 on the whole list.
 */

const ROWS: LeadSummaryRow[] = [
  {
    id: 'lead-old-high',
    session_id: 'sess-old-high',
    created_at: '2026-09-01T09:00:00Z',
    ended_at: '2026-09-01T09:05:00Z',
    call_end_reason: 'buyer_farewell',
    ended_cleanly: true,
    language: 'en',
    status: 'unreviewed',
    score_total: 90,
    project_ids: ['project-a'],
    contact_present: true,
    analysis_status: 'complete',
  },
  {
    id: 'lead-new-low',
    session_id: 'sess-new-low',
    created_at: '2026-09-05T09:00:00Z',
    ended_at: '2026-09-05T09:05:00Z',
    call_end_reason: 'buyer_farewell',
    ended_cleanly: true,
    language: 'en',
    status: 'qualified',
    score_total: 10,
    project_ids: ['project-b'],
    contact_present: true,
    analysis_status: 'complete',
  },
]

async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

function stubUpstream(payload: unknown) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    (async (input: RequestInfo | URL) => {
      calls.push(String(input))
      return new Response(JSON.stringify(payload), {
        status: 200,
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
  cleanup()
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

async function readWith(search: string): Promise<string> {
  const { readLeadRows } = await import('@/lib/admin/leads.server')
  const calls = stubUpstream([])
  await readLeadRows(
    new Request(`https://demo.example/admin/leads${search}`, {
      headers: { cookie: await session() },
    }),
  )
  expect(calls).toHaveLength(1)
  return calls[0]
}

describe('the filters the list read forwards', () => {
  it('sends the status filter the API accepts', async () => {
    expect(await readWith('?status=qualified')).toContain('status=qualified')
  })

  it('sends language and project_id too, since list_leads takes them', async () => {
    const url = await readWith('?language=ar&project_id=binghatti-skyrise')
    expect(url).toContain('language=ar')
    expect(url).toContain('project_id=binghatti-skyrise')
  })

  it('drops a param the API does not accept rather than passing it through', async () => {
    /*
     * The page's own view state - which column is sorted - is not the API's
     * business. FastAPI ignores unknown query params today, which is a
     * courtesy and not a contract: the day it validates them, a `?sort=score`
     * a reviewer bookmarked turns the whole list into a 422. Forwarding a
     * whitelist means the page can grow UI params without touching the API.
     */
    const url = await readWith('?status=unreviewed&sort=score&direction=descending')
    expect(url).toContain('status=unreviewed')
    expect(url).not.toContain('sort=')
    expect(url).not.toContain('direction=')
  })

  it('refuses a status the API would reject instead of forwarding it', async () => {
    // `LeadStatusFilter` is a closed set of three. A hand-edited URL should
    // read as "no filter" rather than becoming a 422 the page renders as
    // "the admin API answered 422".
    const url = await readWith('?status=deleted')
    expect(url).not.toContain('status=')
  })

  it('sends no query at all when the page has none', async () => {
    const url = await readWith('')
    expect(url).not.toContain('?')
  })
})

describe('the status filter chips', () => {
  async function renderChips(active: string | null) {
    const { LeadFilterChips } = (await load(
      '@/components/admin/lead-filters',
    )) as unknown as {
      LeadFilterChips: (p: { active: string | null; total: number }) => ReactElement
    }
    return render(<LeadFilterChips active={active} total={2} />)
  }

  it('offers one chip per status, plus a way back to all of them', async () => {
    await renderChips(null)
    const group = screen.getByRole('navigation', { name: /filter/i })
    for (const [name, href] of [
      ['All', '/admin/leads'],
      ['Unreviewed', '/admin/leads?status=unreviewed'],
      ['Qualified', '/admin/leads?status=qualified'],
      ['Rejected', '/admin/leads?status=rejected'],
    ] as const) {
      expect(within(group).getByRole('link', { name })).toHaveAttribute('href', href)
    }
  })

  it('marks which filter is in force, so the list is never silently narrowed', async () => {
    /*
     * The failure this prevents is specific: a reviewer follows a link into
     * `?status=unreviewed`, sees three rows, and concludes there are three
     * leads. A filtered list that does not say it is filtered is a list that
     * lies about the size of the dataset.
     */
    await renderChips('unreviewed')
    const group = screen.getByRole('navigation', { name: /filter/i })
    expect(within(group).getByRole('link', { name: 'Unreviewed' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(within(group).getByRole('link', { name: 'All' })).not.toHaveAttribute(
      'aria-current',
    )
  })

  it('marks All when nothing is filtered', async () => {
    await renderChips(null)
    const group = screen.getByRole('navigation', { name: /filter/i })
    expect(within(group).getByRole('link', { name: 'All' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })
})

describe('sorting the list a reviewer is looking at', () => {
  async function renderList(rows: LeadSummaryRow[]) {
    const { LeadList } = (await load('@/components/admin/lead-list')) as unknown as {
      LeadList: (props: { rows: LeadSummaryRow[] }) => ReactElement
    }
    return render(<LeadList rows={rows} />)
  }

  /** The primary cell of each row, in the order they are rendered. */
  function orderOnScreen(): string[] {
    return screen
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getByRole('link').textContent ?? '')
  }

  it('offers When and Score as sortable columns and nothing else', async () => {
    /*
     * Only the two the card names. A sortable header on a badge column
     * promises an order that does not exist - what is "sorted" about a
     * contact that is present or absent - and every extra affordance is one
     * more thing to explain.
     */
    await renderList(ROWS)
    for (const name of ['When', 'Score']) {
      expect(screen.getByRole('columnheader', { name: new RegExp(name, 'i') })).toHaveAttribute(
        'aria-sort',
      )
    }
    expect(
      screen.getByRole('columnheader', { name: /language/i }),
    ).not.toHaveAttribute('aria-sort')
  })

  it('reorders by score when the score header is pressed', async () => {
    await renderList(ROWS)
    // The precondition: the API's own order is what is on screen first, so
    // the reorder below is a change rather than a coincidence.
    expect(orderOnScreen()).toEqual(['project-a', 'project-b'])
    await userEvent.click(
      within(screen.getByRole('columnheader', { name: /score/i })).getByRole('button'),
    )
    // Ascending first: 10 before 90.
    expect(orderOnScreen()).toEqual(['project-b', 'project-a'])
  })

  it('reorders by when the call happened, oldest first on the first press', async () => {
    await renderList(ROWS)
    await userEvent.click(
      within(screen.getByRole('columnheader', { name: /when/i })).getByRole('button'),
    )
    expect(orderOnScreen()).toEqual(['project-a', 'project-b'])
  })

  it('reverses on a second press rather than cycling to unsorted', async () => {
    // An unsorted third state on a two-column sort is a state a reviewer
    // reaches by accident and cannot tell from the API's own order.
    await renderList(ROWS)
    const header = within(screen.getByRole('columnheader', { name: /score/i })).getByRole(
      'button',
    )
    await userEvent.click(header)
    await userEvent.click(header)
    expect(orderOnScreen()).toEqual(['project-a', 'project-b'])
  })

  it('keeps a lead with no score out of the way rather than treating it as zero', async () => {
    /*
     * A pending or failed analysis has no score, and sorting it as 0 would
     * put every un-analysed call at the top of an ascending sort - which is
     * the same "zero reads as uninterested" mistake the Score cell already
     * avoids. They sort last in both directions.
     */
    const pending: LeadSummaryRow = {
      ...ROWS[0],
      id: 'lead-pending',
      session_id: 'sess-pending',
      score_total: null,
      analysis_status: 'pending',
      project_ids: ['project-pending'],
    }
    await renderList([...ROWS, pending])
    const header = within(screen.getByRole('columnheader', { name: /score/i })).getByRole(
      'button',
    )
    await userEvent.click(header)
    expect(orderOnScreen().at(-1)).toBe('project-pending')
    await userEvent.click(header)
    expect(orderOnScreen().at(-1)).toBe('project-pending')
  })
})
