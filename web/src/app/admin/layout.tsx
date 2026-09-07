import './astryx.css'
import { AdminProviders } from './providers'
import { bodyFace, displayFace } from '@/theme/faces'
import type { ReactNode } from 'react'

/**
 * The admin route group's own layout, which is what scopes the theme and its
 * faces to it.
 *
 * The human asked for /admin to look like a SaaS dashboard; AGENTS.md says a
 * SaaS look is wrong for Binghatti. Both hold, because they are about
 * different surfaces: this layout themes /admin and nothing else, and the
 * client-facing demo keeps the ink/brass restraint. In the App Router the CSS
 * a layout imports is only loaded for routes beneath it, so that separation is
 * enforced by the bundler rather than by a convention somebody has to
 * remember, and only visitors to /admin download the two faces.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    // The two font variables have to be declared on an ancestor of everything
    // that reads them, which is why they land here rather than in a component:
    // the theme's `--font-family-*` tokens resolve them, and a custom property
    // that resolves to nothing invalidates the whole declaration rather than
    // falling back to the next family in the stack.
    <div className={`${displayFace.variable} ${bodyFace.variable}`}>
      <AdminProviders>{children}</AdminProviders>
    </div>
  )
}
