import './astryx.css'
import { AdminProviders } from './providers'
import type { ReactNode } from 'react'

/**
 * The admin route group's own layout, which is what scopes Astryx to it.
 *
 * The human asked for /admin to look like a SaaS dashboard; AGENTS.md says a
 * SaaS look is wrong for Binghatti. Both hold, because they are about
 * different surfaces: this layout themes /admin and nothing else, and the
 * client-facing demo keeps the ink/brass restraint. In the App Router the CSS
 * a layout imports is only loaded for routes beneath it, so that separation is
 * enforced by the bundler rather than by a convention somebody has to
 * remember.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return <AdminProviders>{children}</AdminProviders>
}
