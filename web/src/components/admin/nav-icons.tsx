/**
 * The three section glyphs.
 *
 * HAND-DRAWN BECAUSE ASTRYX HAS NO CONTENT ICONS. Its built-in registry is 28
 * names and every one is a UI control - chevrons, close, search, funnel,
 * microphone. There is no grid, no document and nothing person-shaped, so
 * finding G2's "sidebar items are plain text with no icons" cannot be closed
 * by naming one. Three inline paths is the smallest way to close it: no icon
 * package added, nothing fetched at runtime, and no registry to keep in sync.
 *
 * The SVG attributes match `defaultIcons.tsx`'s own conventions exactly - 24x24
 * viewBox, `currentColor` stroke at 1.5, round caps and joins, `1em` box - so
 * these sit at the same optical weight as the chevrons Astryx draws beside
 * them. `aria-hidden` on every one: the nav item's label is the accessible
 * name, and an icon that repeated it would make a screen reader say it twice.
 */

const svgProps = {
  xmlns: 'http://www.w3.org/2000/svg',
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  width: '1em',
  height: '1em',
  'aria-hidden': true as const,
}

/** Overview: four panes, the shape of a summary. */
export function OverviewIcon() {
  return (
    <svg {...svgProps}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
    </svg>
  )
}

/**
 * Leads: a speech bubble, because a lead in this product IS a call.
 *
 * Not a person outline, which was the first draft: every row on that list is a
 * conversation the ambassador had, and half of them have no contact at all.
 * A person icon would promise an identity the record often does not carry.
 */
export function LeadsIcon() {
  return (
    <svg {...svgProps}>
      <path d="M20.5 11.5a7.5 7.5 0 0 1-7.5 7.5 7.9 7.9 0 0 1-2.9-.55L4.5 20.5l1.6-4.2A7.5 7.5 0 1 1 20.5 11.5Z" />
    </svg>
  )
}

/** Knowledge: a document with its lines, the thing a reviewer scopes. */
export function KnowledgeIcon() {
  return (
    <svg {...svgProps}>
      <path d="M6.5 2.5h7l5 5v14h-12Z" />
      <path d="M13.5 2.5v5h5" />
      <path d="M9 13h6M9 16.5h6" />
    </svg>
  )
}
