'use client'

import { AppShell } from '@astryxdesign/core/AppShell'
import { SideNav, SideNavItem } from '@astryxdesign/core/SideNav'
import { SideNavHeading } from '@astryxdesign/core/SideNav'
import { Button } from '@astryxdesign/core/Button'
import { TopNav, TopNavHeading } from '@astryxdesign/core/TopNav'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'

/**
 * The admin dashboard frame: a persistent left nav, a title bar, one content
 * region (task-web-admin-astryx-shell).
 *
 * SCOPE, and it is the whole reason this file can exist: AGENTS.md's "if it
 * looks like a generic SaaS dashboard, it is wrong" governs the CLIENT-FACING
 * demo, which is what the tech lead is shown. The admin surface is an internal
 * tool and the human asked for a SaaS dashboard there in as many words, so it
 * runs on Astryx while /, /talk, /text and /states keep the ink/brass
 * restraint. `app/admin/layout.tsx` is what enforces the separation - the
 * bundler only loads Astryx's CSS for routes beneath it.
 *
 * AppShell owns the skip link, the mobile drawer and the content landmark, so
 * none of those are hand-built here. Below the md breakpoint the side nav
 * moves into a modal drawer with a toggle that reports `aria-expanded`; above
 * it, the nav is inline. That is Astryx's behaviour, not ours, which is why
 * the tests assert the CONTRACT (a named landmark, a current item, a toggle
 * that flips) rather than the markup.
 */

/**
 * The sections, in the order a reviewer works: what happened, then the calls,
 * then what the ambassador may say. `Overview` is last-resort exact-matched
 * because every other href starts with `/admin` too.
 */
const SECTIONS = [
  { href: '/admin', label: 'Overview', exact: true },
  { href: '/admin/leads', label: 'Leads', exact: false },
  { href: '/admin/knowledge', label: 'Knowledge', exact: false },
] as const

function isCurrent(pathname: string, href: string, exact: boolean): boolean {
  if (exact) return pathname === href
  // A prefix, so /admin/leads/<id> still marks Leads: matching exactly would
  // leave a reviewer on a detail page with no section highlighted at all. The
  // trailing slash test stops /admin/leadsomething matching /admin/leads.
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function AdminAppShell({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  const pathname = usePathname()

  return (
    <AppShell
      contentPadding={4}
      topNav={
        <TopNav aria-label="Page">
          <TopNavHeading heading={title} />
        </TopNav>
      }
      sideNav={
        <SideNav
          aria-label="Admin sections"
          header={<SideNavHeading heading="Binghatti ambassador" headingHref="/admin" />}
          footer={
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
          }
        >
          {SECTIONS.map((section) => (
            <SideNavItem
              key={section.href}
              label={section.label}
              href={section.href}
              isSelected={isCurrent(pathname, section.href, section.exact)}
            />
          ))}
        </SideNav>
      }
    >
      {children}
    </AppShell>
  )
}
