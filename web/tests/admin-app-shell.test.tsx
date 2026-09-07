import { cleanup, render, screen, within } from '@testing-library/react'
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
async function renderShell(
  children: ReactElement = <p>Page body</p>,
  // The section label the route being rendered would pass. Defaults to
  // Overview, which is what every case wanted before the top bar carried a
  // trail; a case asserting per-route behaviour has to set it, or it renders
  // /admin/leads with the overview's own heading and the fixture disagrees
  // with the pathname it just set.
  title = 'Overview',
) {
  const { AdminAppShell } = (await load('@/components/admin/app-shell')) as unknown as {
    AdminAppShell: (p: { title: string; children: ReactElement }) => ReactElement
  }
  return render(<AdminAppShell title={title}>{children}</AdminAppShell>)
}

/**
 * The nav item by name, scoped to the sections landmark.
 *
 * SCOPED DELIBERATELY, and the reason is a listed UX change: the top bar now
 * carries a breadcrumb trail, so "Overview" and "Leads" are each the name of
 * TWO links on a detail page - the crumb and the nav item. A bare
 * `getByRole('link', {name})` became ambiguous and threw. Scoping is not a
 * workaround for that: it is the assertion these cases always meant. "The nav
 * item for this section is marked current" is a stronger claim than "some link
 * with this name somewhere on the page is", and the looser version would have
 * passed if the marker had landed on the crumb instead of the nav.
 */
function navLink(name: string): HTMLElement {
  const nav = screen.getByRole('navigation', { name: /admin sections/i })
  return within(nav).getByRole('link', { name })
}

/**
 * The shell inside a build-identity provider, which is how the footer's sha
 * reaches a client component: the admin LAYOUT reads process.env on the server
 * and hands the value down, rather than each page remembering to pass it. A
 * rule every page has to remember is a rule some page will forget - all five
 * admin routes lost their h1 exactly that way once already.
 */
async function renderShellWithSha(commitSha: string | null) {
  const { BuildInfoProvider } = (await load(
    '@/components/admin/build-info',
  )) as unknown as {
    BuildInfoProvider: (p: {
      commitSha: string | null
      children: ReactElement
    }) => ReactElement
  }
  const { AdminAppShell } = (await load('@/components/admin/app-shell')) as unknown as {
    AdminAppShell: (p: { title: string; children: ReactElement }) => ReactElement
  }
  return render(
    <BuildInfoProvider commitSha={commitSha}>
      <AdminAppShell title="Overview">
        <p>Page body</p>
      </AdminAppShell>
    </BuildInfoProvider>,
  )
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
      expect(within(nav).getByRole('link', { name })).toHaveAttribute('href', href)
    }
  })

  it('announces the current section as the current page', async () => {
    pathname = '/admin/leads'
    await renderShell(<p>Page body</p>, 'Leads')
    expect(navLink('Leads')).toHaveAttribute('aria-current', 'page')
    expect(navLink('Overview')).not.toHaveAttribute('aria-current')
  })

  it('marks the section current on a detail page too, not just its index', async () => {
    // /admin/leads/<id> is still Leads. Matching the pathname exactly would
    // leave a reviewer on a detail page with no section highlighted at all.
    pathname = '/admin/leads/lead-1'
    await renderShell(<p>Page body</p>, 'sess-1')
    expect(navLink('Leads')).toHaveAttribute('aria-current', 'page')
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

/**
 * The top bar and the footer (task-web-admin-premium-pass, finding G2).
 *
 * WHAT G2 SAYS IS WRONG, and all three parts are the same mistake: the shell
 * spends its two edges on nothing. The top bar is a grey strip that REPEATS
 * the page's own h1 - "Overview" above "Overview" - so a reviewer three levels
 * into a lead has no idea where they are and nothing to click to get back. The
 * footer floats a bare "Sign out" with no identity beside it and no statement
 * of which build is answering, which is the first question anybody debugging a
 * deployed page asks.
 *
 * THE TRAIL IS ANCESTORS ONLY, AND THAT IS A DELIBERATE DEPARTURE from the
 * card's sketch of "Overview / Leads / <lead>". Ending the trail at the
 * current page puts that page's name in the top bar AND in the h1 directly
 * below it, which is the duplication finding G2 is about, in a new shape. So
 * the trail carries only the levels ABOVE this page - every crumb is a live
 * link to somewhere else - and the page announces itself once, in the h1,
 * which is where a screen reader's document outline looks for it. The root
 * has no ancestors and therefore no trail. One line in `ancestorsOf` reverses
 * this if that call goes the other way.
 *
 * ASTRYX AUTO-MARKS THE LAST CRUMB AS THE CURRENT PAGE. Measured in
 * BreadcrumbItem: an item with no explicit `isCurrent` runs an effect that
 * sets `aria-current="page"` on itself if it is last and no sibling claims it
 * - including when that item is a LINK. On an ancestors-only trail that is a
 * lie a screen reader reads out: the last ancestor would announce itself as
 * the page you are on. `isCurrent={false}` opts each item out, and the case
 * below exists because nothing about deleting that prop looks wrong.
 */
describe('the shell top bar', () => {
  /** The trail, or null when the shell renders none. */
  function trail(): string[] | null {
    const nav = screen.queryByRole('navigation', { name: /breadcrumb/i })
    if (nav === null) return null
    return within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent ?? '')
  }

  it('gives each route a trail of the levels above it, and the root none', async () => {
    for (const [route, expected] of [
      ['/admin', null],
      ['/admin/leads', ['Overview']],
      ['/admin/leads/lead-1', ['Overview', 'Leads']],
      ['/admin/knowledge', ['Overview']],
      ['/admin/knowledge/doc-1', ['Overview', 'Knowledge']],
    ] as const) {
      pathname = route
      await renderShell()
      // The precondition, so a shell that rendered nothing at all cannot pass
      // the `null` row by accident: there is always a main landmark.
      expect(screen.getByRole('main'), route).toBeInTheDocument()
      expect(trail(), route).toEqual(expected === null ? null : [...expected])
      cleanup()
    }
  })

  it('points each crumb at the level it names', async () => {
    pathname = '/admin/leads/lead-1'
    await renderShell()
    const nav = screen.getByRole('navigation', { name: /breadcrumb/i })
    expect(within(nav).getByRole('link', { name: 'Overview' })).toHaveAttribute(
      'href',
      '/admin',
    )
    expect(within(nav).getByRole('link', { name: 'Leads' })).toHaveAttribute(
      'href',
      '/admin/leads',
    )
  })

  it('lets no crumb claim to be the current page', async () => {
    /*
     * Astryx marks the last item current by itself unless told otherwise (see
     * the note above). On a trail of ancestors that would announce the section
     * a reviewer came FROM as the page they are on.
     */
    pathname = '/admin/leads/lead-1'
    await renderShell()
    const nav = screen.getByRole('navigation', { name: /breadcrumb/i })
    // Positive first: the trail is really there and really has two items, so
    // the negative below is a claim about them rather than about an empty nav.
    expect(within(nav).getAllByRole('link')).toHaveLength(2)
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(0)
  })

  it('stops the top bar repeating the page heading', async () => {
    /*
     * The finding itself: "the top bar is an empty grey strip repeating the h1
     * ('Overview' twice)". AppShell wraps its header region in role="banner",
     * so this asks the exact question - is the page's own title printed up
     * there as well as in the content?
     */
    for (const [route, heading] of [
      ['/admin', 'Overview'],
      ['/admin/leads', 'Leads'],
      ['/admin/knowledge', 'Knowledge'],
    ] as const) {
      pathname = route
      await renderShell(<p>Page body</p>, heading)
      const banner = screen.getByRole('banner')
      // Positive precondition: the h1 really does say this, so the negative is
      // about a DUPLICATE and not about a page that renders no title at all.
      expect(
        screen.getByRole('heading', { level: 1, name: heading }),
        route,
      ).toBeInTheDocument()
      expect(within(banner).queryByText(heading), route).toBeNull()
      cleanup()
    }
  })
})

describe('the shell footer', () => {
  const FULL_SHA = '3bf4ec63d9a1f0e2b7c4a5968d3e1f2a0b9c8d7e'

  it('says which build is answering, with the whole sha in reach', async () => {
    /*
     * "BUILT FROM", not "running": the web service redeploys on web/** and
     * data/** only, so a merge touching neither leaves this at the previous
     * commit - which is the true identity of the running image and the same
     * one the deploy sweeps print. The short form is what a human reads; the
     * full sha stays on the element so nobody has to retype a prefix into a
     * `git show`.
     */
    await renderShellWithSha(FULL_SHA)
    const element = screen.getByTitle(FULL_SHA)
    expect(element).toBeInTheDocument()
    expect(element.textContent).toContain('3bf4ec6')
    expect(element.textContent).toMatch(/built from/i)
  })

  it('omits the line entirely when the platform did not set one', async () => {
    /*
     * Locally, in CI and in any test there is no RAILWAY_GIT_COMMIT_SHA, and a
     * placeholder would be worse than silence: a reviewer reading "dev" on a
     * deployed page learns something false, where a reviewer seeing no line
     * learns only that the page is not saying - which is true.
     */
    await renderShellWithSha(null)
    // Positive precondition: the footer IS rendered and Sign out is in it, so
    // "no sha line" is a fact about this footer rather than about no footer.
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
    expect(screen.queryByText(/built from/i)).toBeNull()
    expect(screen.queryByTitle(FULL_SHA)).toBeNull()
  })

  it('names who is signed in beside the way out', async () => {
    /*
     * G2: "Sign out floats at the bottom with no separator or identity". There
     * is no per-person identity in this deployment - one shared access code -
     * so the honest line names the SESSION, not a user. Saying "Signed in" and
     * nothing more is what the footer can truthfully claim.
     */
    await renderShellWithSha(null)
    const footerText = screen.getByRole('button', { name: /sign out/i })
      .closest('[data-admin-identity]')
    expect(footerText).not.toBeNull()
    expect(footerText?.textContent).toMatch(/signed in/i)
  })
})

/**
 * The overview a reviewer can start work from (finding G3).
 *
 * G3: "six bare label+number cards in a 3x2 grid and nothing else; cards do
 * not link anywhere; no 'needs attention' list; no recent activity; empty page
 * below the fold." A count nobody can click is a fact with no next step - it
 * tells a reviewer there are four unreviewed leads and leaves them to find the
 * list, filter it themselves and work out which four.
 *
 * TWO PANELS, NOT THREE, AND THAT IS A RULING RATHER THAN A SHORTCUT. The card
 * asks Needs attention for unreviewed leads, documents awaiting scope AND
 * figures awaiting approval, "all from the existing list reads; no new API
 * route". The first two are derivable - `status === 'unreviewed'` and
 * `status === 'draft'`. The third is not: `list_documents` selects no figure
 * or chunk counts and there is NO route that lists figures across documents,
 * so the only ways to get it are an N+1 of detail reads on this render or a
 * new aggregate route, and the card forbids the second. god ruled the two
 * honest panels ship and a `figures_pending` count becomes an API card. The
 * structural case below pins the count at two on purpose: an empty "figures
 * awaiting approval" panel, empty because nobody asked the database, reads as
 * "nothing needs approval" - a false statement on a reviewer's screen, and
 * worse than no panel.
 */
describe('the overview needs-attention panel', () => {
  async function renderOverview(leads: LeadSummaryRow[], documents: DocumentRow[]) {
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

  function leadsNeeding(count: number): LeadSummaryRow[] {
    return Array.from({ length: count }, (_unused, index) => ({
      ...LEAD,
      id: `lead-${index}`,
      session_id: `sess-${index}`,
      status: 'unreviewed' as const,
      project_ids: [`project-${index}`],
    }))
  }

  it('links every count card to the list it counts', async () => {
    /*
     * The point of the number is the rows behind it. "Unreviewed 4" that a
     * reviewer cannot click leaves them to open Leads, find the filter and
     * reconstruct the same four by hand.
     */
    await renderOverview(
      [LEAD, { ...LEAD, id: 'l2', status: 'qualified' }],
      [DOC],
    )
    for (const [label, href] of [
      ['Leads', '/admin/leads'],
      ['Unreviewed', '/admin/leads?status=unreviewed'],
      ['Qualified', '/admin/leads?status=qualified'],
      ['Rejected', '/admin/leads?status=rejected'],
      ['Documents', '/admin/knowledge'],
    ] as const) {
      const card = screen.getByText(label).closest('[data-count-card]')
      expect(card, label).not.toBeNull()
      expect(within(card as HTMLElement).getByRole('link'), label).toHaveAttribute(
        'href',
        href,
      )
    }
  })

  it('lists the unreviewed leads themselves, each one openable', async () => {
    await renderOverview(leadsNeeding(3), [])
    const panel = screen.getByRole('region', { name: /unreviewed leads/i })
    const links = within(panel).getAllByRole('link')
    expect(links).toHaveLength(3)
    expect(links[0]).toHaveAttribute('href', '/admin/leads/lead-0')
  })

  it('stops at five and says how many it is not showing', async () => {
    /*
     * A panel is a starting point, not a second lead list. Eight unreviewed
     * leads listed in full would push the rest of the page below the fold -
     * which is the other half of what G3 complains about.
     */
    await renderOverview(leadsNeeding(8), [])
    const panel = screen.getByRole('region', { name: /unreviewed leads/i })
    expect(within(panel).getAllByRole('link')).toHaveLength(5)
    expect(within(panel).getByText(/3 more/i)).toBeInTheDocument()
  })

  it('does not say "and more" when it is showing all of them', async () => {
    await renderOverview(leadsNeeding(2), [])
    const panel = screen.getByRole('region', { name: /unreviewed leads/i })
    expect(within(panel).getAllByRole('link')).toHaveLength(2)
    expect(within(panel).queryByText(/more/i)).toBeNull()
  })

  it('lists the documents waiting to be scoped', async () => {
    await renderOverview(
      [],
      [DOC, { ...DOC, id: 'd2', status: 'published', published_at: '2026-09-07T06:00:00Z' }],
    )
    const panel = screen.getByRole('region', { name: /awaiting scope/i })
    const links = within(panel).getAllByRole('link')
    // Only the draft: a published document needs nothing from a reviewer.
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', '/admin/knowledge/doc-1')
  })

  it('says so in words when a panel has nothing in it', async () => {
    /*
     * An empty panel with no sentence is indistinguishable from a panel whose
     * read failed - the same reason the count cards render zeroes rather than
     * disappearing.
     */
    await renderOverview([{ ...LEAD, status: 'qualified' }], [])
    expect(
      within(screen.getByRole('region', { name: /unreviewed leads/i })).getByText(
        /nothing is waiting/i,
      ),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: /awaiting scope/i })).getByText(
        /every document has been scoped/i,
      ),
    ).toBeInTheDocument()
  })

  it('shows exactly the two panels it can honestly populate', async () => {
    /*
     * See the note above this describe block. A third panel for figures
     * awaiting approval would be empty because no list read carries figure
     * state, and a reviewer would read that emptiness as "nothing needs
     * approval". Pinned so the panel cannot be added without the data.
     */
    await renderOverview(leadsNeeding(1), [DOC])
    const panels = screen.getAllByRole('region')
    expect(panels).toHaveLength(2)
    expect(screen.queryByText(/figures? awaiting approval/i)).toBeNull()
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
    /*
     * The number is read from the card that carries the label, not from the
     * document: two cards showing 0 would make a bare getByText('0')
     * ambiguous and the test would pass on the wrong card.
     *
     * SEARCHED WITHIN THE COUNT CARDS rather than across the page, which the
     * needs-attention panels forced and which the helper always meant. A
     * document-wide `getByText(/unreviewed/i)` now matches the card's label
     * AND the "Unreviewed leads" panel heading, so it threw on two matches.
     * Scoping it to the cards is what the name says it does.
     */
    const cards = Array.from(document.querySelectorAll('[data-count-card]'))
    expect(cards.length, 'no count cards rendered').toBeGreaterThan(0)
    const card = cards.find((candidate) =>
      label.test(candidate.querySelector('p')?.textContent ?? ''),
    )
    expect(card, `no count card labelled ${String(label)}`).not.toBeUndefined()
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
