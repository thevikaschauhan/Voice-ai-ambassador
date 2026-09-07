import { Card } from '@astryxdesign/core/Card'
import { Text } from '@astryxdesign/core/Text'
import type { DocumentRow } from '@/lib/admin/knowledge'
import type { LeadSummaryRow } from '@/lib/admin/leads'

/**
 * The overview landing: what is waiting, in numbers (task-web-admin-astryx-shell).
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
 */

function CountCard({ label, count }: { label: string; count: number }) {
  return (
    <Card data-count-card>
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
    </Card>
  )
}

export function OverviewCards({
  leads,
  documents,
}: {
  leads: LeadSummaryRow[]
  documents: DocumentRow[]
}) {
  const counts = [
    { label: 'Leads', count: leads.length },
    { label: 'Unreviewed', count: leads.filter((l) => l.status === 'unreviewed').length },
    { label: 'Qualified', count: leads.filter((l) => l.status === 'qualified').length },
    { label: 'Rejected', count: leads.filter((l) => l.status === 'rejected').length },
    { label: 'Documents', count: documents.length },
    { label: 'Published', count: documents.filter((d) => d.status === 'published').length },
  ]

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {counts.map((entry) => (
        <CountCard key={entry.label} label={entry.label} count={entry.count} />
      ))}
    </div>
  )
}
