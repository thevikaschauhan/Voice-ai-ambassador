'use client'

import Link from 'next/link'
import { Badge } from '@astryxdesign/core/Badge'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Table, proportional, useTableSortable } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import { endReasonLabel } from '@/lib/admin/leads'
import type { LeadSummaryRow } from '@/lib/admin/leads'
import { useMemo, useState } from 'react'
import { LeadStatusBadge } from './status-badge'
import { ListScore } from './score'
import { relativeAge } from './age'

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
 * How a row names itself, most human first (finding G4).
 *
 * G4: "row identity is a session id ('sess-demo-a') styled as an underlined
 * link". An opaque id is the one thing on the row that means nothing to the
 * person reading it, and it was the row's headline.
 *
 * THE CONTACT NAME TIER THE CARD ASKS FOR IS DELIBERATELY ABSENT, and it is a
 * privacy boundary rather than an omission. `list_leads` names its columns so
 * that it cannot leak one - "contact_name, contact_phone and contact_email are
 * absent from that list. A list page cannot leak a transcript it was never
 * handed" - and docs/10 draws the same line. Widening that projection to put a
 * buyer's name on a list would undo what three layers of this codebase are
 * written to prevent, and it is not a decision a restyle gets to make. The
 * Contact column still says WHETHER a contact exists, which is the operational
 * bit and not a value.
 *
 * "Caller" plus the short id promises nothing the record does not carry. A
 * person's name or a person icon would imply an identity that half these rows
 * do not have.
 */
function rowName(row: LeadSummaryRow): string {
  const project = row.project_ids[0]
  if (project !== undefined && project !== '') return project
  return `Caller ${row.id.slice(0, 8)}`
}

/** The sort state Astryx's headless plugin reports back. */
type SortState = { sortKey: string; direction: 'ascending' | 'descending' }[]

/**
 * The rows in the order the reviewer asked for.
 *
 * CLIENT-SIDE, and that is the card's own split: the status filter narrows the
 * QUERY because `list_leads` takes it, and sort reorders rows already on
 * screen, where a round trip to reorder fifty visible rows would be a round
 * trip for nothing.
 *
 * A LEAD WITH NO SCORE SORTS LAST IN BOTH DIRECTIONS, never as zero. Treating
 * null as 0 puts every un-analysed call at the top of an ascending sort, which
 * is the same mistake the Score cell already refuses to make: zero reads as
 * "this buyer was uninterested" when what happened is that nobody knows yet.
 * "Last" is the honest position for an unknown in an ordering, in either
 * direction - reversing the sort should not promote the rows that have no
 * value to sort by.
 */
function sorted(rows: readonly LeadSummaryRow[], sort: SortState): LeadSummaryRow[] {
  const primary = sort[0]
  if (primary === undefined) return [...rows]
  const factor = primary.direction === 'ascending' ? 1 : -1

  return [...rows].sort((left, right) => {
    if (primary.sortKey === 'score') {
      // Null is not a value, so it does not take part in the comparison; it
      // takes the end. `factor` is applied only to the two-number case.
      if (left.score_total === null && right.score_total === null) return 0
      if (left.score_total === null) return 1
      if (right.score_total === null) return -1
      return (left.score_total - right.score_total) * factor
    }
    // `when`: the string comparison is safe because these are ISO-8601 UTC
    // instants from Postgres, which sort lexicographically in time order.
    // Parsing them to compare would add a failure mode for no gain.
    return left.created_at.localeCompare(right.created_at) * factor
  })
}

export function LeadList({ rows }: { rows: readonly LeadSummaryRow[] }) {
  /*
    Astryx's sort plugin is HEADLESS: it owns the header affordance, the
    aria-sort attribute and the click-to-cycle, and reports the next state
    back - the data is ours to reorder. `allowUnsortedState` stays false on
    purpose: a third unsorted state on a two-column sort is one a reviewer
    reaches by accident and cannot tell apart from the API's own order.
  */
  const [sort, setSort] = useState<SortState>([])
  // Parameterised on the row type: the hook is generic over
  // `T extends Record<string, unknown>` and defaults to that, which is not
  // assignable to `TablePlugin<TableRow>` - the plugin's transforms are
  // contravariant in T, so the default widens and tsc rejects it.
  const sortPlugin = useTableSortable<TableRow>({ sort, onSortChange: setSort })
  // Memoised on the two things it depends on, so re-rendering the shell around
  // this list does not re-sort every row.
  const ordered = useMemo(() => sorted(rows, sort), [rows, sort])

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
    <div className="flex flex-col gap-3">
      {/*
        The count. A reviewer filtering a list needs to know how many rows
        answered, and G4 lists its absence. It lives HERE rather than in the
        filter chips - the page renders those directly above, so the two read
        as one row on screen, and duplicating the number would give a filtered
        list two places to disagree about its own size.

        Singular is a real branch, not a nicety: "1 calls" is how a screen
        starts looking unfinished.
      */}
      <Text as="p" display="block" type="supporting" color="secondary">
        {rows.length === 1 ? '1 call' : `${rows.length} calls`}
      </Text>

      <Table<TableRow>
        // Copied rather than cast: `data` is a mutable T[] and the prop here is
        // readonly, and a cast would have claimed a mutability the caller never
        // granted.
        data={ordered as TableRow[]}
        // `idKey`, not a getRowKey callback: the row identity prop takes a key
        // name or a function, and it is what keeps React's reconciliation stable
        // when the list reorders.
        idKey="id"
        density="balanced"
        hasHover
        /*
          THE WHOLE ROW IS THE TARGET (finding G4: "only the id is clickable").
          Astryx's Table exposes no row-level href or onClick - measured, its
          TableProps has neither - but `transformBodyRow` in its plugin
          pipeline is the supported way to put props on each <tr>, so this
          needs no wrapper and no event delegation of our own.

          The row gets a POINTER affordance and a click handler; it does NOT
          get a link role or a tabindex. The keyboard and screen-reader target
          stays the single real <a> in the primary cell, because a <tr> that
          announces itself as a link is a lie about the markup, and putting an
          <a> in every cell would make a screen reader read one destination
          seven times. So: mouse users get the row, everyone else gets the
          link, and there is exactly one accessible name per row.

          A click inside a real link or button is left alone - otherwise the
          row handler would fire alongside the link's own navigation, and a
          future action button in a cell would navigate away when pressed.

          THE HANDLER CLICKS THE ROW'S OWN LINK rather than calling
          `router.push`, and that is a considered choice, not a trick. Three
          reasons. It keeps this presentational component free of
          `next/navigation` - the first draft used `useRouter` and immediately
          broke `admin-call-end-reason.test.tsx`, which renders this list and
          knows nothing about routers, with "invariant expected app router to
          be mounted"; every future test rendering a lead list would have paid
          the same tax. It gives the destination ONE source, so the row and
          the link cannot drift apart. And it goes through next/link's own
          handler, so a row click behaves exactly like a click on the link -
          same client navigation, same scroll and prefetch behaviour - instead
          of a second code path that resembles it.
        */
        plugins={{
          sort: sortPlugin,
          rowLink: {
            transformBodyRow: (props, row) => {
              const href = `/admin/leads/${(row as TableRow).id}`
              return {
                ...props,
                htmlProps: {
                  ...props.htmlProps,
                  // The destination on the row itself, which is also what the
                  // test reads: an attribute is checkable where a bound
                  // closure is not.
                  'data-row-href': href,
                  className: `${props.htmlProps.className ?? ''} cursor-pointer`.trim(),
                  onClick: (event) => {
                    if (!(event.currentTarget instanceof HTMLElement)) return
                    if (
                      event.target instanceof Element &&
                      event.target.closest('a, button') !== null
                    ) {
                      return
                    }
                    // The row's single link, which is also the only thing that
                    // knows where the row goes.
                    event.currentTarget.querySelector('a')?.click()
                  },
                },
              }
            },
          },
        }}
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
                {/*
                  The row's one link, and its accessible name is the human
                  handle rather than the id. `hasUnderline` is not set: an
                  underline on every row of a table is noise, and the row's
                  hover state plus the pointer cursor already say it is
                  clickable. Measured in PR B: next/link gets no Astryx
                  styling of its own, so the class carries the colour.
                */}
                <Link
                  className="text-[var(--color-text-primary)] no-underline"
                  href={`/admin/leads/${row.id}`}
                >
                  {rowName(row)}
                </Link>
                <div className="flex flex-wrap items-center gap-2">
                  {/*
                    The session id stays - a reviewer cross-referencing a log
                    needs it - as supporting text rather than as the headline.
                  */}
                  <Text as="span" type="supporting" color="secondary">
                    {row.session_id}
                  </Text>
                  <Text as="span" type="supporting" color="secondary">
                    {endReasonLabel(row.call_end_reason)}
                  </Text>
                  {row.ended_cleanly ? null : (
                    // The word, not a colour: a badge whose meaning is only its
                    // variant is a badge a colour-blind reviewer cannot read.
                    // NEUTRAL, not warning: a call the buyer cut short is a
                    // fact about the call, not a problem to fix (G4 counts the
                    // yellow as one of three colour systems).
                    <Badge variant="neutral" label="incomplete" />
                  )}
                </div>
              </div>
            ),
          },
          {
            key: 'when',
            header: 'When',
            width: proportional(1),
            sortable: true,
            renderCell: (row: TableRow) => (
              /*
                ONE LINE (G4: "the When column wraps to three lines"). A
                relative age is what a reviewer scans by; the exact instant
                stays on `title` and in `dateTime`, so nothing
                machine-readable is lost and the precise time is one hover
                away. The duration moves to its own column rather than
                stacking under the date.

                A native <time>, because Text's `as` accepts only
                div/span/p/label/h1-h3 - and dropping the element would drop
                the machine-readable timestamp with it.
              */
              <time
                className="whitespace-nowrap text-[13px]"
                dateTime={row.created_at}
                title={row.created_at}
              >
                {relativeAge(row.created_at)}
              </time>
            ),
          },
          {
            key: 'duration',
            header: 'Length',
            width: proportional(1),
            renderCell: (row: TableRow) => (
              <Text as="span" type="supporting" color="secondary">
                {duration(row)}
              </Text>
            ),
          },
          {
            key: 'language',
            header: 'Language',
            width: proportional(1),
            renderCell: (row: TableRow) => (
              // Uppercased in the STRING, not by a CSS text-transform: the cell
              // used to carry "en" in the DOM and show "EN" on screen, the same
              // mismatch the status badge fixes.
              <Text as="span">{row.language.toUpperCase()}</Text>
            ),
          },
          {
            key: 'score',
            header: 'Score',
            width: proportional(1),
            sortable: true,
            renderCell: (row: TableRow) =>
              row.analysis_status === 'failed' ? (
                // Not a zero: zero reads as an uninterested buyer, and what
                // happened is that nobody knows yet.
                <Badge variant="error" label="analysis failed" />
              ) : row.score_total === null ? (
                <Badge variant="neutral" label="pending" />
              ) : (
                // Shared with the detail's total, so one number never carries
                // two colour vocabularies; see `./score`.
                <ListScore total={row.score_total} />
              ),
          },
          {
            key: 'contact',
            header: 'Contact',
            width: proportional(1),
            renderCell: (row: TableRow) =>
              // Whether, never what. Neutral: a captured contact is a fact,
              // not something a reviewer must act on.
              row.contact_present ? (
                <Badge variant="neutral" label="captured" />
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
            renderCell: (row: TableRow) => <LeadStatusBadge status={row.status} />,
          },
        ]}
      />
    </div>
  )
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
