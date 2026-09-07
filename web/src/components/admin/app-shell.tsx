'use client'

import { AppShell } from '@astryxdesign/core/AppShell'
import { Breadcrumbs, BreadcrumbItem } from '@astryxdesign/core/Breadcrumbs'
import { SideNav, SideNavItem } from '@astryxdesign/core/SideNav'
import { TopNav } from '@astryxdesign/core/TopNav'
import { SideNavHeading } from '@astryxdesign/core/SideNav'
import { Button } from '@astryxdesign/core/Button'
import { StatusDot } from '@astryxdesign/core/StatusDot'
import { Text } from '@astryxdesign/core/Text'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'

import { useCommitSha } from '@/components/admin/build-info'
import { KnowledgeIcon, LeadsIcon, OverviewIcon } from '@/components/admin/nav-icons'
import { shortSha } from '@/lib/admin/build-info'

/**
 * The admin dashboard frame: a persistent left nav, a wayfinding bar, one
 * content region (task-web-admin-astryx-shell, then finding G2).
 *
 * SCOPE, and it is the whole reason this file can exist: AGENTS.md's "if it
 * looks like a generic SaaS dashboard, it is wrong" governs the CLIENT-FACING
 * demo, which is what the tech lead is shown. The admin surface is an internal
 * tool and the human asked for a SaaS dashboard there in as many words, so it
 * runs on Astryx while /, /talk, /text and /states keep the ink/brass
 * restraint. `app/admin/layout.tsx` is what enforces the separation - the
 * bundler only loads the theme's CSS for routes beneath it.
 *
 * AppShell owns the skip link, the mobile drawer and the content landmark, so
 * none of those are hand-built here. Below the md breakpoint the side nav
 * moves into a modal drawer with a toggle that reports `aria-expanded`; above
 * it, the nav is inline. That is Astryx's behaviour, not ours, which is why
 * the tests assert the CONTRACT (a named landmark, a current item, a toggle
 * that flips) rather than the markup.
 *
 * WHAT FINDING G2 CHANGED HERE, all three of them at the shell's edges:
 *
 *   the top bar was a grey strip printing the page's own h1 a second time.
 *   It is now a trail of the levels ABOVE this page plus a session indicator.
 *
 *   the nav items were plain text with a grey pill for the current one. They
 *   now carry a glyph each and a brass marker.
 *
 *   the footer floated a bare "Sign out". It now names the session, separates
 *   itself, and states which build is answering.
 */

/**
 * The sections, in the order a reviewer works: what happened, then the calls,
 * then what the ambassador may say. `Overview` is last-resort exact-matched
 * because every other href starts with `/admin` too.
 */
const SECTIONS = [
  { href: '/admin', label: 'Overview', exact: true, Icon: OverviewIcon },
  { href: '/admin/leads', label: 'Leads', exact: false, Icon: LeadsIcon },
  { href: '/admin/knowledge', label: 'Knowledge', exact: false, Icon: KnowledgeIcon },
] as const

function isCurrent(pathname: string, href: string, exact: boolean): boolean {
  if (exact) return pathname === href
  // A prefix, so /admin/leads/<id> still marks Leads: matching exactly would
  // leave a reviewer on a detail page with no section highlighted at all. The
  // trailing slash test stops /admin/leadsomething matching /admin/leads.
  return pathname === href || pathname.startsWith(`${href}/`)
}

/**
 * The levels ABOVE this page, nearest last. Empty at the root.
 *
 * ANCESTORS ONLY, which is a departure from the card's sketch of "Overview /
 * Leads / <lead>" and the one design call in this file worth arguing. A trail
 * that ends at the current page prints that page's name in the top bar and
 * again in the h1 directly beneath it - which is the duplication finding G2 is
 * about, wearing a breadcrumb. Carrying only the ancestors means every crumb
 * is a live link to somewhere else, and the page announces itself once, in the
 * h1, where a screen reader's document outline looks for it.
 *
 * To go back to a trail that includes the current page: append the page's own
 * crumb with `isCurrent` here. One line, and `Breadcrumbs` already handles the
 * rest.
 */
function ancestorsOf(pathname: string): { href: string; label: string }[] {
  const below = pathname.replace(/^\/admin\/?/, '').split('/').filter(Boolean)
  // The root is its own page and has nothing above it, so it gets no trail at
  // all rather than a one-item trail pointing at itself.
  if (below.length === 0) return []
  const trail = [{ href: '/admin', label: 'Overview' }]
  // A detail route sits under a section, so the section is an ancestor too.
  // A section index does not: its only ancestor is the overview.
  if (below.length >= 2) {
    const section = SECTIONS.find((entry) => entry.href === `/admin/${below[0]}`)
    if (section !== undefined) trail.push({ href: section.href, label: section.label })
  }
  return trail
}

export function AdminAppShell({
  title,
  heading,
  children,
}: {
  /** The short section label. */
  title: string
  /**
   * The page h1, when the page's subject is not its section label.
   *
   * A detail route needs both: the trail says which SECTION this page sits
   * under, the h1 says WHICH RECORD ("sess-1"). Before this existed, a detail
   * page carried its own h1 for the record and the shell added a second one
   * for the section - two level-one headings, and a screen reader user
   * navigating by h1 got two page titles, neither of which was the page.
   * Defaults to `title`, so a list page passes one string and gets one h1.
   */
  heading?: string
  children: ReactNode
}) {
  const pathname = usePathname()
  const ancestors = ancestorsOf(pathname)
  const sha = useCommitSha()

  return (
    <AppShell
      contentPadding={4}
      /*
        ASTRYX'S TopNav, NOT A PLAIN DIV, and this is the most important line
        in the file. Below the md breakpoint AppShell puts its topNav into
        "mobile-bar" mode through a context, and it is TopNav ITSELF that reads
        that context and renders the hamburger that opens the nav drawer. A
        plain <div> does not read it, so replacing TopNav silently removed the
        only way to reach the navigation on a phone - the whole nav, not a
        decoration. Caught by the existing drawer case from PR A, which is
        exactly the regression that case was written for.

        The slots are chosen for that same mobile behaviour: `heading` and
        `endContent` are the two TopNav keeps in the bar, while
        `startContent`/`children` are hidden and folded into the drawer. So
        the trail goes in `heading` - wayfinding matters most on a small
        screen - and the session indicator in `endContent`, beside the toggle.
      */
      topNav={
        <TopNav
          aria-label="Page"
          heading={
            /*
              The trail, or nothing at the root. Rendering an empty
              <Breadcrumbs> would leave a named navigation landmark with no
              items in it, which a screen reader user can tab to and learn
              nothing from.
            */
            ancestors.length > 0 ? (
              <Breadcrumbs label="Breadcrumb" variant="supporting">
                {ancestors.map((crumb) => (
                  <BreadcrumbItem
                    key={crumb.href}
                    href={crumb.href}
                    /*
                      MEASURED IN BreadcrumbItem, not a precaution: an item
                      with no explicit `isCurrent` runs an effect that sets
                      `aria-current="page"` on itself if it is last and no
                      sibling claims it - including when the item is a LINK. On
                      a trail of ancestors that would announce the section the
                      reviewer came FROM as the page they are on. Astryx's
                      model assumes a trail ending at the current page; this
                      one does not, so every item opts out. Do not delete this.
                    */
                    isCurrent={false}
                  >
                    {crumb.label}
                  </BreadcrumbItem>
                ))}
              </Breadcrumbs>
            ) : null
          }
          endContent={
            /*
              The session indicator. A dot and no words, because the words
              belong in the footer beside the control that acts on them -
              saying "Signed in" in both places would be the shell repeating
              itself again, which is the habit finding G2 is about.

              `variant="accent"` is brass, not the green `success` would give.
              The direction allows one accent and lists "a current state"
              among the things it may mark; a second hue here would be a second
              colour system on a surface whose point is that it has one.
              `label` is required by StatusDot and becomes the aria-label, so
              the meaning reaches a screen reader without a hover.
            */
            <StatusDot variant="accent" label="Signed in" tooltip="Signed in" />
          }
        />
      }
      sideNav={
        <SideNav
          aria-label="Admin sections"
          header={<SideNavHeading heading="Binghatti" headingHref="/admin" />}
          footer={
            /*
              G2: "Sign out floats at the bottom with no separator or
              identity". All three are answered here - a hairline rule to
              separate it from the sections, the session named above the
              control that ends it, and the build underneath.

              There is no per-person identity in this deployment: one shared
              access code, no user record. So the line names the SESSION and
              claims nothing about who is holding it, which is all the page can
              truthfully say.
            */
            <div
              data-admin-identity
              className="flex flex-col gap-1 border-t border-[var(--color-border)] pt-3"
            >
              <Text as="span" type="supporting" color="secondary">
                Signed in
              </Text>
              <Button
                label="Sign out"
                variant="ghost"
                onClick={() => {
                  // The existing logout route and the existing reload: the
                  // session is an HttpOnly cookie, so the server has to
                  // re-render the page to see that it is gone.
                  void fetch('/api/admin/logout', { method: 'POST' }).then(() =>
                    window.location.reload(),
                  )
                }}
              />
              {/*
                Which build is answering. Omitted entirely when the platform
                set no sha - see lib/admin/build-info.ts for why a placeholder
                would be worse than silence. The short form is what a human
                reads; the FULL sha is on the `title` so nobody has to retype a
                prefix into a `git show`.
              */}
              {/*
                `typeof` rather than `sha !== null`, and that is not
                belt-and-braces: the provider's prop is typed `string | null`,
                but a caller that omits it passes `undefined`, which is not
                null, so the line rendered and `shortSha(undefined)` threw -
                inside the shell every admin page renders. A footer that
                crashes takes down five routes to avoid printing seven
                characters. Same lesson as the lead detail's 500: degrade,
                never throw, and never trust the type at a boundary a caller
                can get wrong.
              */}
              {typeof sha === 'string' && sha !== '' ? (
                // The `title` is on the span rather than on Text: TextProps
                // does not declare it (measured - tsc rejects it), and Astryx
                // does not spread unknown props here the way TextInput does.
                <span title={sha}>
                  <Text as="span" type="supporting" color="secondary">
                    built from {shortSha(sha)}
                  </Text>
                </span>
              ) : null}
            </div>
          }
        >
          {SECTIONS.map((section) => (
            <SideNavItem
              key={section.href}
              label={section.label}
              href={section.href}
              icon={<section.Icon />}
              isSelected={isCurrent(pathname, section.href, section.exact)}
            />
          ))}
        </SideNav>
      }
    >
      {/*
        The page h1, owned by the shell rather than by each page. AppShell
        renders no heading of its own - its docs say the first heading in the
        content area is the page h1 - and neither the trail nor the nav label
        is a heading. Putting it here means no page can forget it, which is
        exactly how all five admin routes lost their h1 when the per-page
        headers were folded into this shell.
      */}
      <div className="flex flex-col gap-6">
        <Text as="h1" type="display-3">
          {heading ?? title}
        </Text>
        {children}
      </div>
    </AppShell>
  )
}
