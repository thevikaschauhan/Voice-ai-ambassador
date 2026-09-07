import Link from 'next/link'
import { Card } from '@astryxdesign/core/Card'
import { Text } from '@astryxdesign/core/Text'
import type { DocumentRow } from '@/lib/admin/knowledge'
import type { LeadSummaryRow } from '@/lib/admin/leads'
import { relativeAge } from './age'

/**
 * The overview landing: what is waiting, and a way into it (findings G3).
 *
 * DERIVED FROM THE TWO LIST READS THE ADMIN ALREADY DOES, deliberately: no new
 * API route, no count endpoint, no second source of truth for a number. The
 * pages that own those lists already fetch them, so the overview costs nothing
 * the surface was not already paying and can never disagree with the list a
 * reviewer opens next.
 *
 * THE LABELS ARE THE CLOSURE'S OWN WORDS. `LeadStatus` is unreviewed /
 * qualified / rejected, so the card says "Unreviewed" and not "Pending" -
 * `AnalysisStatus` already owns the word pending, and a screen that invents a
 * synonym for a status disagrees with the API it displays (#113 taught this
 * the hard way with `inventory_governed`).
 *
 * Zeroes are rendered rather than omitted: an admin who sees no card cannot
 * tell "no leads yet" from "the read failed".
 *
 * EVERY COUNT NOW LINKS TO THE ROWS BEHIND IT (G3: "cards do not link
 * anywhere"). A number a reviewer cannot click is a fact with no next step -
 * "Unreviewed 4" told them there were four and left them to open Leads, find
 * the filter and reconstruct the same four by hand. The status filters those
 * links use are the ones `list_leads` already accepts.
 *
 * TWO PANELS, NOT THREE. The card asks Needs attention for unreviewed leads,
 * documents awaiting scope and figures awaiting approval, all from the
 * existing list reads. The first two are derivable from `status`; the third is
 * not - `list_documents` selects no figure or chunk counts and no route lists
 * figures across documents, only `GET /v1/knowledge/documents/{id}` per
 * document. An N+1 of detail reads on this render was the alternative to a new
 * route the card forbids, so god ruled the two honest panels ship and a
 * `figures_pending` count on the document list projection becomes an API card.
 * A third panel would have been empty because nobody asked the database, and a
 * reviewer reads that emptiness as "nothing needs approval" - which is false.
 */

/** How many rows a panel shows before it stops being a starting point. */
const PANEL_LIMIT = 5

function CountCard({
  label,
  count,
  href,
}: {
  label: string
  count: number
  /** The list this number came from, filtered the way the number was. */
  href: string
}) {
  return (
    <Card data-count-card>
      {/*
        The whole card is the link, and the link's accessible name carries the
        label AND the number - "Unreviewed, 4" - so a screen reader user
        hearing the link knows what it counts without reading the card first.
      */}
      <Link href={href} className="flex flex-col gap-1 no-underline">
        {/*
          `display="block"` on both, because Astryx's Text is display:inline by
          DEFAULT and the CSS wins over the tag - `as="p"` alone rendered
          "Leads0" on one line. Caught in the browser, not by a test: a unit
          test reading textContent sees the label and the number either way.
        */}
        <Text as="p" display="block" type="supporting" color="secondary">
          {label}
        </Text>
        <Text as="p" display="block" type="display-3" data-count>
          {count}
        </Text>
      </Link>
    </Card>
  )
}

/**
 * One panel of things waiting, or one sentence saying nothing is.
 *
 * THE EMPTY SENTENCE IS NOT DECORATION. An empty panel with no words is
 * indistinguishable from a panel whose read failed - the same reason the count
 * cards render zeroes rather than disappearing. `role="region"` with a name
 * makes each panel a landmark a screen reader user can jump to, which is what
 * turns two lists on a page into two places.
 */
function AttentionPanel({
  heading,
  emptySentence,
  items,
}: {
  heading: string
  emptySentence: string
  items: { id: string; href: string; primary: string; secondary: string }[]
}) {
  const shown = items.slice(0, PANEL_LIMIT)
  const hidden = items.length - shown.length

  return (
    <Card>
      <section aria-label={heading} className="flex flex-col gap-3">
        {/*
          `type="large"`, not a heading type: Astryx's `BuiltinTextType` is
          body/large/label/supporting/code/display-1..3 and has NO heading-N
          member - measured, tsc rejects "heading-4". A heading's LEVEL comes
          from `as`, and its size from a type; `display-3` here would make a
          panel heading the same size as the page's own h1.
        */}
        <Text as="h2" display="block" type="large">
          {heading}
        </Text>

        {shown.length === 0 ? (
          <Text as="p" display="block" type="supporting" color="secondary">
            {emptySentence}
          </Text>
        ) : (
          <ul className="flex flex-col gap-2">
            {shown.map((item) => (
              <li key={item.id}>
                <Link
                  href={item.href}
                  className="flex flex-col gap-0.5 text-[var(--color-text-primary)] no-underline"
                >
                  <span className="text-[14px]">{item.primary}</span>
                  <Text as="span" type="supporting" color="secondary">
                    {item.secondary}
                  </Text>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {/*
          Only when there is a remainder, and it is not a link: it says how
          much this panel is NOT showing, and the card above already leads to
          the filtered list. A "3 more" that navigated somewhere would be a
          second, quieter route to the same place.
        */}
        {hidden > 0 ? (
          <Text as="p" display="block" type="supporting" color="secondary">
            {`and ${hidden} more`}
          </Text>
        ) : null}
      </section>
    </Card>
  )
}

/**
 * How a lead names itself in a panel.
 *
 * The same order the list uses, and for the same reason - the project a buyer
 * named is the most human handle this projection carries, and a contact name
 * is deliberately not in it. Kept in step with `lead-list.tsx` by hand rather
 * than shared, because a panel row and a table row want different secondary
 * text: here the age, there the ending reason.
 */
function leadName(lead: LeadSummaryRow): string {
  const project = lead.project_ids[0]
  if (project !== undefined && project !== '') return project
  return `Caller ${lead.id.slice(0, 8)}`
}

export function OverviewCards({
  leads,
  documents,
}: {
  leads: LeadSummaryRow[]
  documents: DocumentRow[]
}) {
  const counts = [
    { label: 'Leads', count: leads.length, href: '/admin/leads' },
    {
      label: 'Unreviewed',
      count: leads.filter((l) => l.status === 'unreviewed').length,
      href: '/admin/leads?status=unreviewed',
    },
    {
      label: 'Qualified',
      count: leads.filter((l) => l.status === 'qualified').length,
      href: '/admin/leads?status=qualified',
    },
    {
      label: 'Rejected',
      count: leads.filter((l) => l.status === 'rejected').length,
      href: '/admin/leads?status=rejected',
    },
    { label: 'Documents', count: documents.length, href: '/admin/knowledge' },
    {
      label: 'Published',
      count: documents.filter((d) => d.status === 'published').length,
      // The knowledge list takes no status filter, so this is the honest
      // destination rather than an invented `?status=published` the API would
      // ignore. If a filter lands there, this is the line that changes.
      href: '/admin/knowledge',
    },
  ]

  const unreviewed = leads
    .filter((lead) => lead.status === 'unreviewed')
    .map((lead) => ({
      id: lead.id,
      href: `/admin/leads/${lead.id}`,
      primary: leadName(lead),
      secondary: relativeAge(lead.created_at),
    }))

  const awaitingScope = documents
    // A draft is a document whose chunks nobody has scoped yet, which is
    // exactly what this panel is asking a reviewer to do. `parsing` resolves
    // itself and `failed` needs a re-upload, not a scope decision.
    .filter((document) => document.status === 'draft')
    .map((document) => ({
      id: document.id,
      href: `/admin/knowledge/${document.id}`,
      primary: document.title,
      secondary: relativeAge(document.created_at),
    }))

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {counts.map((entry) => (
          <CountCard
            key={entry.label}
            label={entry.label}
            count={entry.count}
            href={entry.href}
          />
        ))}
      </div>

      {/*
        Needs attention, below the numbers: the counts say how much, these say
        which. Two columns above lg so neither panel is below the fold on a
        laptop - G3's "empty page below the fold" was partly that there was
        nothing there and partly that what little there was sat too low.
      */}
      <div className="grid gap-4 lg:grid-cols-2">
        <AttentionPanel
          heading="Unreviewed leads"
          emptySentence="Nothing is waiting for a decision."
          items={unreviewed}
        />
        <AttentionPanel
          heading="Documents awaiting scope"
          emptySentence="Every document has been scoped."
          items={awaitingScope}
        />
      </div>
    </div>
  )
}
