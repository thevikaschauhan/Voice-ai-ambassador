'use client'

import Link from 'next/link'
import { LinkProvider } from '@astryxdesign/core/Link'
import { Theme } from '@astryxdesign/core/theme'
import { neutralTheme } from '@astryxdesign/theme-neutral/built'
import type { ReactNode } from 'react'

/**
 * Astryx's theme, mounted for the admin route group only.
 *
 * `LinkProvider` hands Astryx next/link, so a SideNavItem with an `href`
 * client-navigates instead of reloading the document - without it every nav
 * click would be a full page load and the admin would feel worse than the
 * two-card page it replaced.
 *
 * Dark mode is Astryx's own: `neutralTheme` carries both palettes and follows
 * `prefers-color-scheme`, so there is no toggle to keep in sync and no second
 * source of truth for a colour.
 */
export function AdminProviders({ children }: { children: ReactNode }) {
  return (
    <Theme theme={neutralTheme}>
      <LinkProvider component={Link}>{children}</LinkProvider>
    </Theme>
  )
}
