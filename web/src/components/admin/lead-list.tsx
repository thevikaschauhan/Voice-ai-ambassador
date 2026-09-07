'use client'

import Link from 'next/link'
import { Badge } from '@astryxdesign/core/Badge'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Table, proportional } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import { endReasonLabel } from '@/lib/admin/leads'
import type { LeadStatus, LeadSummaryRow } from '@/lib/admin/leads'

/**
 * MEASURED CONSTRAINT, not a preference: Astryx's Table is generic over
 * `T extends Record<string, unknown>`, and `LeadSummaryRow` is an INTERFACE -
 * TypeScript gives interfaces no implicit index signature, so the row type the
 * rest of the admin uses cannot satisfy that bound on its own. The
 * intersection adds the signature without weakening a single field, so
 * `row.session_id` stays typed inside every cell. Do not "fix" this by
 * loosening the row type to Record<string, unknown>: the cells would then read
 * `unknown` and every field access would need a cast of its own.
 */
type TableRow = LeadSummaryRow & Record<string, unknown>

/**
 * Every call that finished, as a table of operational facts.
 *
 * docs/10- draws the line this component exists to hold: status, score,
 * language, project ids, call time, completeness and whether a contact was
 * captured - and NOTHING a buyer said. Buyer words and contact values live on
 * the detail page, which somebody has to choose to open. A transcript line in a
 * list is a transcript nobody chose to read, and a phone number in a list is a
 * phone number in the next screenshot.
 *
 * Two things are shown that a tidier table would drop, because dropping them is
 * how a truncated call gets mistaken for a complete one: whether the call ended
 * cleanly, and whether the analysis failed. A failed analysis is NOT a score of
 * zero - zero would read as "this buyer was uninterested" when what happened is
 * that nobody knows yet.
 */

/**
 * The status words, and the variant that carries each one's weight.
 *
 * SPELLED OUT HERE rather than derived from the enum by capitalising it,
 * because the display word is a decision and the enum is an API value. They
 * agree with /admin's overview cards on purpose: one surface, one spelling of
 * one status. Reading `row.status` into the cell and leaving a CSS
 * `text-transform` to make it look like a label is what this replaces - it put
 * "unreviewed" in the DOM and "UNREVIEWED" on the screen.
 */
const STATUS: Record<LeadStatus, { label: string; variant: 'neutral' | 'success' | 'error' }> = {
  unreviewed: { label: 'Unreviewed', variant: 'neutral' },
  qualified: { label: 'Qualified', variant: 'success' },
  rejected: { label: 'Rejected', variant: 'error' },
}

export function LeadList({ rows }: { rows: readonly LeadSummaryRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        // The same two sentences the paragraph carried, in the same order, now
        // a title and a description: the first is what happened, the second is
        // what to expect. EmptyState gives the first one a heading, which is
        // what a screen reader user navigating an empty page had nothing to
        // land on before.
        title="No calls have been recorded yet."
        description="A lead appears here as soon as a call ends, including one that was cut short."
      />
    )
  }

  return (
    <Table<TableRow>
      // Copied rather than cast: `data` is a mutable T[] and the prop here is
      // readonly, and a cast would have claimed a mutability the caller never
      // granted.
      data={[...rows] as TableRow[]}
      // `idKey`, not a getRowKey callback: the row identity prop takes a key
      // name or a function, and it is what keeps React's reconciliation stable
      // when the list reorders.
      idKey="id"
      density="balanced"
      hasHover
      // Every column declares a width: Astryx skips the minimum-width floor
      // for a column that does not, which collapses columns on a narrow
      // screen instead of scrolling the table.
      columns={[
        {
          key: 'session',
          header: 'Call',
          width: proportional(2),
          renderCell: (row: TableRow) => (
            <div className="flex flex-col gap-1">
              <Link className="underline decoration-current/40" href={`/admin/leads/${row.id}`}>
                {row.session_id}
              </Link>
              <div className="flex flex-wrap items-center gap-2">
                <Text as="span" type="supporting" color="secondary">
                  {endReasonLabel(row.call_end_reason)}
                </Text>
                {row.ended_cleanly ? null : (
                  // The word, not a colour: a badge whose meaning is only its
                  // variant is a badge a colour-blind reviewer cannot read.
                  <Badge variant="warning" label="incomplete" />
                )}
              </div>
            </div>
          ),
        },
        {
          key: 'when',
          header: 'When',
          width: proportional(1),
          renderCell: (row: TableRow) => (
            <div className="flex flex-col gap-1">
              {/*
                A native <time>, because Text's `as` accepts only
                div/span/p/label/h1-h3 - and dropping the element would drop
                the machine-readable timestamp with it.
              */}
              <time className="text-[12px]" dateTime={row.created_at}>
                {when(row.created_at)}
              </time>
              <Text as="span" type="supporting" color="secondary">
                {duration(row)}
              </Text>
            </div>
          ),
        },
        {
          key: 'language',
          header: 'Language',
          width: proportional(1),
          renderCell: (row: TableRow) => <Text as="span">{row.language}</Text>,
        },
        {
          key: 'projects',
          header: 'Projects',
          width: proportional(1),
          renderCell: (row: TableRow) =>
            row.project_ids.length === 0 ? (
              <Text as="span" color="secondary">
                none named
              </Text>
            ) : (
              <Text as="span">{row.project_ids.join(', ')}</Text>
            ),
        },
        {
          key: 'score',
          header: 'Score',
          width: proportional(1),
          renderCell: (row: TableRow) =>
            row.analysis_status === 'failed' ? (
              // Not a zero: zero reads as an uninterested buyer, and what
              // happened is that nobody knows yet.
              <Badge variant="error" label="analysis failed" />
            ) : row.score_total === null ? (
              <Badge variant="neutral" label="pending" />
            ) : (
              <Text as="span" type="display-3" display="block">
                {row.score_total}
              </Text>
            ),
        },
        {
          key: 'contact',
          header: 'Contact',
          width: proportional(1),
          renderCell: (row: TableRow) =>
            // Whether, never what.
            row.contact_present ? (
              <Badge variant="info" label="captured" />
            ) : (
              <Text as="span" color="secondary">
                none
              </Text>
            ),
        },
        {
          key: 'status',
          header: 'Status',
          width: proportional(1),
          renderCell: (row: TableRow) => (
            <Badge variant={STATUS[row.status].variant} label={STATUS[row.status].label} />
          ),
        },
      ]}
    />
  )
}

function when(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toISOString().slice(0, 16).replace('T', ' ')
}

/** Whole seconds, because a demo call is measured in them. */
function duration(row: LeadSummaryRow): string {
  if (row.ended_at === null) return 'no end recorded'
  const seconds = Math.round(
    (new Date(row.ended_at).getTime() - new Date(row.created_at).getTime()) / 1000,
  )
  if (!Number.isFinite(seconds) || seconds < 0) return 'no end recorded'
  const minutes = Math.floor(seconds / 60)
  return minutes === 0 ? `${seconds}s` : `${minutes}m ${seconds % 60}s`
}
