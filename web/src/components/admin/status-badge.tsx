'use client'

import { Badge } from '@astryxdesign/core/Badge'
import type { LeadStatus } from '@/lib/admin/leads'
import type { DocumentStatus } from '@/lib/admin/knowledge'

/**
 * The status vocabularies, and the weight each word carries.
 *
 * ONE MODULE BECAUSE THREE SURFACES SHOW THE SAME STATUS: the lead list, the
 * lead detail's header and its decision history. They were rendering
 * `row.status` straight from the API under a CSS `uppercase`, so the DOM said
 * "unreviewed" while the screen said "UNREVIEWED" and /admin's overview cards
 * already said "Unreviewed" - three spellings of one status, none of them the
 * closure's. Keeping the map here is what stops them drifting apart again.
 *
 * Both maps are typed on their full enum, so a status added to either
 * vocabulary fails the build here rather than rendering in the neutral style.
 * That is how `parsing` and `archived` came to look exactly like a draft.
 *
 * The label is a decision and the enum is an API value, which is why these are
 * written out rather than derived by capitalising the value.
 */

type Variant = 'neutral' | 'info' | 'success' | 'warning' | 'error'

const LEAD: Record<LeadStatus, { label: string; variant: Variant }> = {
  unreviewed: { label: 'Unreviewed', variant: 'neutral' },
  qualified: { label: 'Qualified', variant: 'success' },
  rejected: { label: 'Rejected', variant: 'error' },
}

const DOCUMENT: Record<DocumentStatus, { label: string; variant: Variant }> = {
  parsing: { label: 'Parsing', variant: 'info' },
  draft: { label: 'Draft', variant: 'neutral' },
  published: { label: 'Published', variant: 'success' },
  failed: { label: 'Failed', variant: 'error' },
  archived: { label: 'Archived', variant: 'neutral' },
}

export function LeadStatusBadge({ status }: { status: LeadStatus }) {
  return <Badge variant={LEAD[status].variant} label={LEAD[status].label} />
}

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  return <Badge variant={DOCUMENT[status].variant} label={DOCUMENT[status].label} />
}
