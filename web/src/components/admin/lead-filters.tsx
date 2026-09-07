'use client'

import Link from 'next/link'
import type { LeadStatus } from '@/lib/admin/leads'

/**
 * The status filter, as links (finding G4: "no filters ... or count").
 *
 * LINKS, NOT BUTTONS, and the choice follows from where the filtering happens.
 * `list_leads` takes a `status` filter, so this narrows the QUERY rather than
 * the rows already downloaded - which is the difference between a page that
 * works at 60k leads and one that fetches them all to hide most. A different
 * query is a different URL, so the control that changes it is a link:
 * bookmarkable, shareable, openable in a new tab, and working before any
 * JavaScript has run. A SegmentedControl would have looked more like a filter
 * bar and been a worse one.
 *
 * THE ACTIVE CHIP IS MARKED, and the failure that guards against is specific:
 * a reviewer follows a link into `?status=unreviewed`, sees three rows, and
 * concludes there are three leads. A filtered list that does not say it is
 * filtered lies about the size of the dataset. `aria-current="page"` is the
 * same mechanism the section nav uses, so a screen reader announces it without
 * a visual cue, and the brass border is the visual half.
 */

const CHIPS: { label: string; status: LeadStatus | null }[] = [
  { label: 'All', status: null },
  // In the order a reviewer works, which is the order the nav uses: the thing
  // needing attention first, then the two settled outcomes.
  { label: 'Unreviewed', status: 'unreviewed' },
  { label: 'Qualified', status: 'qualified' },
  { label: 'Rejected', status: 'rejected' },
]

function hrefFor(status: LeadStatus | null): string {
  // No status means the unfiltered list, and its URL is the bare path rather
  // than `?status=` - an empty param would be forwarded as a filter that
  // matches nothing.
  return status === null ? '/admin/leads' : `/admin/leads?status=${status}`
}

/**
 * THE COUNT IS NOT HERE, and that was a deliberate reversal. It sat beside
 * these chips at first, which reads well - "Unreviewed | 3 calls" - and gave
 * a filtered list TWO places that could disagree about its own size, since
 * `LeadList` renders the count for every list whether or not a filter bar is
 * above it. One owner; the page puts these directly above the list, so they
 * still read as one row on screen.
 */
export function LeadFilterChips({
  active,
}: {
  /** The status in force, or null for the unfiltered list. */
  active: string | null
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/*
        A named navigation landmark, so a screen reader user can reach the
        filter without walking the table. `nav` rather than a group because
        every child navigates.
      */}
      <nav aria-label="Filter by status" className="flex flex-wrap items-center gap-2">
        {CHIPS.map((chip) => {
          const isActive = chip.status === active || (chip.status === null && active === null)
          return (
            <Link
              key={chip.label}
              href={hrefFor(chip.status)}
              // `aria-current`, not `aria-pressed`: these are links to other
              // views, and pressed is for a control that toggles in place.
              aria-current={isActive ? 'page' : undefined}
              className={
                isActive
                  ? // Brass border and brass text, no fill: the same rule the
                    // primary button and the active nav item follow, so a
                    // reviewer learns one visual language for "this one".
                    'rounded-[var(--radius-element)] border border-[var(--color-accent)] px-3 py-1 text-[13px] text-[var(--color-accent)] no-underline'
                  : 'rounded-[var(--radius-element)] border border-[var(--color-border)] px-3 py-1 text-[13px] text-[var(--color-text-secondary)] no-underline'
              }
            >
              {chip.label}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
