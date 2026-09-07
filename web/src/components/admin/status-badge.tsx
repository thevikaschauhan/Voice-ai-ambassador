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
 *
 * ONE COLOUR SYSTEM, THREE WEIGHTS (finding G4, which counts three systems
 * running at once on the lead list). The rule is about what a reviewer must
 * DO, not about how a word feels:
 *
 *   neutral   a state nobody has to act on, including finished work
 *   accent    a state that needs a reviewer - brass, the one accent
 *   error     a failure or a rejection, and nothing else
 *
 * So `unreviewed` takes the brass: it is the entire point of this screen. And
 * `qualified` goes NEUTRAL rather than green, which is the change that will
 * look wrong at first glance and is right - a qualified lead is work that is
 * finished, and a screen that celebrates finished work in green while the
 * thing needing attention sits in grey has its emphasis backwards. Green also
 * bought a fourth hue for information the word already carries.
 *
 * `info` and `warning` are gone from both maps. Every remaining badge keeps
 * its word, so the colour is redundant rather than load-bearing.
 */

/**
 * `accent` is a variant THIS THEME ADDS - Astryx ships
 * neutral/info/success/warning/error plus categorical hues and nothing that
 * means "needs a reviewer". `astryx theme build` writes the module
 * augmentation from `binghatti.theme.ts`, so it typechecks here rather than
 * needing a cast; see that file for why it is a tint and a border rather than
 * a fill.
 */
type Variant = 'neutral' | 'accent' | 'error'

const LEAD: Record<LeadStatus, { label: string; variant: Variant }> = {
  unreviewed: { label: 'Unreviewed', variant: 'accent' },
  qualified: { label: 'Qualified', variant: 'neutral' },
  rejected: { label: 'Rejected', variant: 'error' },
}

const DOCUMENT: Record<DocumentStatus, { label: string; variant: Variant }> = {
  // A draft needs a reviewer to scope it, which is this vocabulary's own
  // "needs attention"; parsing is a state that resolves itself.
  parsing: { label: 'Parsing', variant: 'neutral' },
  draft: { label: 'Draft', variant: 'accent' },
  published: { label: 'Published', variant: 'neutral' },
  failed: { label: 'Failed', variant: 'error' },
  archived: { label: 'Archived', variant: 'neutral' },
}

export function LeadStatusBadge({ status }: { status: LeadStatus }) {
  return <Badge variant={LEAD[status].variant} label={LEAD[status].label} />
}

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  return <Badge variant={DOCUMENT[status].variant} label={DOCUMENT[status].label} />
}
