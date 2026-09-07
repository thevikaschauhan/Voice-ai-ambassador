import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { DocumentRow } from '@/lib/admin/knowledge'
import type { LeadSummaryRow } from '@/lib/admin/leads'

/**
 * The admin app shell on Astryx (task-web-admin-astryx-shell).
 *
 * The human asked for /admin to look like a SaaS dashboard with a left nav.
 * AGENTS.md says a SaaS look is wrong for Binghatti; god scoped that rule to
 * the CLIENT-FACING demo, so both hold and this surface adopts Astryx.
 *
 * WHAT THESE CASES PROTECT is the accessibility contract, not the appearance.
 * A design system can be swapped, restyled or upgraded through beta; what must
 * not silently change is that there is one named navigation landmark, that the
 * current section is announced as current, that the small-screen drawer says
 * whether it is open, and that Sign out is reachable. Every one of those is a
 * thing a screen reader or a keyboard user depends on and a screenshot cannot
 * show.
 *
 * Astryx facts measured here rather than assumed (its own docs disagree with
 * two of them, so they are asserted):
 *   - AppShell's content landmark is `role="main"` on a div, NOT a <main>
 *     element as the README says. `getByRole('main')` finds it either way,
 *     which is why the case asserts the ROLE.
 *   - SideNav takes no `label` prop; `aria-label` passes through to its <nav>.
 *   - the drawer only exists below the md breakpoint, and jsdom's matchMedia
 *     is stubbed `matches: false` in tests/setup.ts, so a case that wants the
 *     toggle has to say it is on a small screen first.
 */

async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

/**
 * What a screen size looks like to Astryx: whether the media query matches.
 *
 * RESET IN `beforeEach`, and that is not tidiness. `Object.defineProperty`
 * survives `vi.restoreAllMocks()`, so one case switching to a small screen
 * left every later case rendering the drawer CLOSED - and a closed drawer
 * hides the side nav, so "Sign out is reachable" failed as though the
 * component had dropped it. A leaked global reads exactly like a defect in
 * whatever runs next.
 */
function onAScreenWhere(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

let pathname = '/admin'

vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
}))

/**
 * Default children are deliberately NOT a heading named after the section. The
 * case below asserts the SHELL renders the page h1, so a child
 * <h1>Overview</h1> would satisfy it on the fixture's own markup - it would
 * pass with the shell rendering no heading at all, which is the one thing it
 * exists to catch.
 */
async function renderShell(children: ReactElement = <p>Page body</p>) {
  const { AdminAppShell } = (await load('@/components/admin/app-shell')) as unknown as {
    AdminAppShell: (p: { title: string; children: ReactElement }) => ReactElement
  }
  return render(<AdminAppShell title="Overview">{children}</AdminAppShell>)
}

/**
 * Copied from the real interfaces, not invented from the field names I
 * expected. My first draft of both fixtures was wrong in three ways and tsc
 * caught it - the same mechanism that shipped a guessed response shape twice
 * in #109 and #113. A fixture is a claim about a contract; take it from the
 * type.
 */
const LEAD: LeadSummaryRow = {
  id: 'lead-1',
  session_id: 'sess-1',
  created_at: '2026-09-07T05:00:00Z',
  ended_at: '2026-09-07T05:07:30Z',
  call_end_reason: 'buyer_farewell',
  ended_cleanly: true,
  language: 'en',
  status: 'unreviewed',
  score_total: 40,
  project_ids: ['binghatti-skyrise'],
  contact_present: false,
  analysis_status: 'complete',
}

const DOC: DocumentRow = {
  id: 'doc-1',
  revision: 1,
  title: 'Payment plan note',
  source_type: 'paste',
  status: 'draft',
  parse_error_code: null,
  created_at: '2026-09-07T05:00:00Z',
  published_at: null,
}

beforeEach(() => {
  pathname = '/admin'
  onAScreenWhere(false)
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the admin app shell', () => {
  it('offers one named navigation landmark carrying the three sections', async () => {
    await renderShell()
    const nav = screen.getByRole('navigation', { name: /admin sections/i })
    expect(nav).toBeInTheDocument()
    for (const [name, href] of [
      ['Overview', '/admin'],
      ['Leads', '/admin/leads'],
      ['Knowledge', '/admin/knowledge'],
    ] as const) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('href', href)
    }
  })

  it('announces the current section as the current page', async () => {
    pathname = '/admin/leads'
    await renderShell()
    expect(screen.getByRole('link', { name: 'Leads' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute(
      'aria-current',
    )
  })

  it('marks the section current on a detail page too, not just its index', async () => {
    // /admin/leads/<id> is still Leads. Matching the pathname exactly would
    // leave a reviewer on a detail page with no section highlighted at all.
    pathname = '/admin/leads/lead-1'
    await renderShell()
    expect(screen.getByRole('link', { name: 'Leads' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('renders the page content inside the main landmark', async () => {
    await renderShell(<p>The lead list goes here</p>)
    expect(screen.getByRole('main')).toContainElement(
      screen.getByText('The lead list goes here'),
    )
  })

  it('says whether the small-screen drawer is open, and flips when pressed', async () => {
    onAScreenWhere(true)
    await renderShell()
    const toggle = screen.getByRole('button', { name: /open navigation/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls')
    await userEvent.click(toggle)
    expect(
      screen.getByRole('button', { name: /open navigation/i }),
    ).toHaveAttribute('aria-expanded', 'true')
  })

  it('gives the page a level-one heading naming the section', async () => {
    /*
     * FOUND BY THE PRODUCTION BROWSER RUN, not by the cases above, and it was
     * a regression I introduced: wrapping the pages in the shell removed the
     * per-page <header> that carried each <h1>, and the first text on every
     * admin page became "Skip to content" with no heading anywhere.
     *
     * AppShell deliberately renders no heading - its own docs say the first
     * heading in the content area is the page h1 - and the title in the top
     * bar is a nav label, not a heading. So the shell owes the content region
     * one, or a screen reader user has no document outline to navigate by and
     * "jump to heading" lands nowhere.
     *
     * Asserted on the SHELL rather than page by page for the same reason the
     * regression happened: a rule each page has to remember is a rule some
     * page will forget.
     */
    await renderShell()
    const heading = screen.getByRole('heading', { level: 1, name: 'Overview' })
    expect(screen.getByRole('main')).toContainElement(heading)
  })

  it('takes a page heading distinct from the section label when given one', async () => {
    /*
     * A detail route needs both: the top bar says what kind of page this is,
     * the h1 says which record. Without this the page had to choose between a
     * useless h1 ("Lead") and a second h1 of its own - and the lead detail
     * chose the second one, which is how /admin/leads/<id> came to ship two.
     */
    const { AdminAppShell } = (await load('@/components/admin/app-shell')) as unknown as {
      AdminAppShell: (p: {
        title: string
        heading?: string
        children: ReactElement
      }) => ReactElement
    }
    render(
      <AdminAppShell title="Lead" heading="sess-1">
        <p>Page body</p>
      </AdminAppShell>,
    )
    const heading = screen.getByRole('heading', { level: 1, name: 'sess-1' })
    expect(screen.getByRole('main')).toContainElement(heading)
    // One h1, not two: the section label stays a nav label.
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('keeps Sign out reachable', async () => {
    await renderShell()
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('signs out through the existing logout route and reloads', async () => {
    const sent: string[] = []
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      sent.push(String(input))
      return new Response(null, { status: 204 })
    }) as typeof fetch)
    await renderShell()
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))
    expect(sent).toEqual(['/api/admin/logout'])
  })
})

describe('the overview counts', () => {
  async function renderCards(leads: LeadSummaryRow[], documents: DocumentRow[]) {
    const { OverviewCards } = (await load(
      '@/components/admin/overview-cards',
    )) as unknown as {
      OverviewCards: (p: {
        leads: LeadSummaryRow[]
        documents: DocumentRow[]
      }) => ReactElement
    }
    return render(<OverviewCards leads={leads} documents={documents} />)
  }

  function countUnder(label: RegExp): string {
    // The number is read from the card that carries the label, not from the
    // document: two cards showing 0 would make a bare getByText('0') ambiguous
    // and the test would pass on the wrong card.
    const card = screen.getByText(label).closest('[data-count-card]')
    expect(card).not.toBeNull()
    return card?.querySelector('[data-count]')?.textContent ?? ''
  }

  it('counts leads by the status vocabulary the closure uses', async () => {
    await renderCards(
      [
        LEAD,
        { ...LEAD, id: 'l2', status: 'qualified' },
        { ...LEAD, id: 'l3', status: 'qualified' },
        { ...LEAD, id: 'l4', status: 'rejected' },
      ],
      [],
    )
    expect(countUnder(/^leads$/i)).toBe('4')
    // 'Unreviewed', not 'Pending': `LeadStatus` says unreviewed, and
    // `AnalysisStatus` already owns the word pending. One vocabulary per
    // closure, or the screen disagrees with the API it is displaying (#113).
    expect(countUnder(/unreviewed/i)).toBe('1')
    expect(countUnder(/qualified/i)).toBe('2')
    expect(countUnder(/rejected/i)).toBe('1')
  })

  it('counts documents and how many are published', async () => {
    await renderCards(
      [],
      [DOC, { ...DOC, id: 'd2', status: 'published', published_at: '2026-09-07T06:00:00Z' }],
    )
    expect(countUnder(/^documents$/i)).toBe('2')
    expect(countUnder(/published/i)).toBe('1')
  })

  it('shows zeroes rather than nothing when a list is empty', async () => {
    // An admin who sees no cards cannot tell "no leads" from "the read failed".
    await renderCards([], [])
    expect(countUnder(/^leads$/i)).toBe('0')
    expect(countUnder(/^documents$/i)).toBe('0')
  })
})
