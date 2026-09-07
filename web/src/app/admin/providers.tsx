'use client'

import Link from 'next/link'
import { LinkProvider } from '@astryxdesign/core/Link'
import { Theme } from '@astryxdesign/core/theme'
import { BuildInfoProvider } from '@/components/admin/build-info'
import { binghattiTheme } from '@/theme/binghatti'
import type { ReactNode } from 'react'

/**
 * The Binghatti theme, mounted once for the whole admin route group.
 *
 * ONE THEME, BOTH SURFACES (finding G1). This provider sits in the route
 * group's layout, so the sign-in door and the dashboard are inside the same
 * one - which is the fix for /admin having shipped as two products: the
 * dashboard painted Astryx's light neutral surface while the door painted
 * nothing and let the root ink-950 body show through behind it.
 *
 * `mode="dark"` IS A DECISION, not a default. Astryx's `mode="system"` hands
 * the product's identity to `prefers-color-scheme`, which means /admin looked
 * like a different product per visitor and any contrast ratio measured for it
 * described only the machine it was measured on. Pinned, the palette in
 * `binghatti.theme.ts` is the palette every reviewer sees and the AA ratios in
 * the PR body are facts about the product. It also matches the demo surface,
 * whose `globals.css` sets `color-scheme: dark` outright.
 *
 * `LinkProvider` hands Astryx next/link, so a SideNavItem or a breadcrumb with
 * an `href` client-navigates instead of reloading the document.
 *
 * `BuildInfoProvider` carries the commit sha the layout read on the server
 * down to the shell's footer. It is here rather than in each page for the
 * reason the h1 taught: a rule every page has to remember is a rule some page
 * will forget, and all five admin routes lost their heading that way at once.
 */
export function AdminProviders({
  commitSha,
  children,
}: {
  /** The sha this service was built from, or null when unset. */
  commitSha: string | null
  children: ReactNode
}) {
  return (
    <Theme theme={binghattiTheme} mode="dark">
      <LinkProvider component={Link}>
        <BuildInfoProvider commitSha={commitSha}>{children}</BuildInfoProvider>
      </LinkProvider>
    </Theme>
  )
}
