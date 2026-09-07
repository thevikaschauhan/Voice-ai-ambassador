'use client'

import Link from 'next/link'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Table, proportional } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import { PARSE_ERROR_ADVICE, PARSE_ERROR_LABELS } from '@/lib/admin/knowledge'
import { sourceLabel } from '@/lib/admin/knowledge'
import type { DocumentRow } from '@/lib/admin/knowledge'
import { DocumentStatusBadge } from './status-badge'
import { relativeAge } from './age'

/**
 * The documents the ambassador may draw on, with their status.
 *
 * A failed parse is shown with WHAT failed and WHAT TO DO about it, because
 * `docs/10-` step 2 makes that the point of the status: a scanned PDF ends as
 * `no_extractable_text` and the admin has to be told that scans need OCR and
 * that OCR is deferred. A bare "failed" sends somebody to re-upload the same
 * file.
 */

/** See lead-list.tsx: Astryx's Table needs an index signature its rows lack. */
type TableRow = DocumentRow & Record<string, unknown>


export function DocumentList({ rows }: { rows: readonly DocumentRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No documents yet."
        description="Paste a paragraph or upload a PDF, DOCX or TXT to start."
      />
    )
  }

  const published = rows.filter((row) => row.status === 'published').length

  return (
    <div className="flex flex-col gap-3">
      {/*
        BOTH NUMBERS, because they answer different questions: how much is in
        the library, and how much of it the ambassador may actually draw on. A
        library of forty drafts and one published document is a very different
        state from forty published ones, and the list showed neither.

        Singular is a real branch: "1 documents" is how a screen starts
        looking unfinished.
      */}
      <Text as="p" display="block" type="supporting" color="secondary">
        {rows.length === 1 ? '1 document' : `${rows.length} documents`}
        {`, ${published} published`}
      </Text>

      <Table<TableRow>
        data={[...rows] as TableRow[]}
        idKey="id"
        density="balanced"
        hasHover
        columns={[
          {
            key: 'title',
            header: 'Document',
            width: proportional(2),
            renderCell: (row: TableRow) => (
              <div className="flex flex-col gap-1">
                <Link className="underline decoration-current/40" href={`/admin/knowledge/${row.id}`}>
                  {row.title}
                </Link>
                <Text as="span" type="supporting" color="secondary">
                  revision {row.revision}
                </Text>
              </div>
            ),
          },
          {
            key: 'source_type',
            header: 'Source',
            width: proportional(1),
            renderCell: (row: TableRow) => (
              // The spelled label, in the DOM as it appears on screen - no CSS
              // text-transform, which is the mismatch the status badge fixes.
              <Text as="span">{sourceLabel(row.source_type)}</Text>
            ),
          },
          {
            key: 'created_at',
            header: 'Added',
            width: proportional(1),
            renderCell: (row: TableRow) => (
              /*
                A relative age to scan, the exact UTC instant on `title` and in
                `dateTime`. G6 names "timestamps without zone", and it is not a
                detail: "2026-09-03 09:00" alone is unreadable across a team in
                two places, and Dubai or UTC decides whether a document landed
                before or after the call that needed it.

                A native <time>: Text's `as` has no time tag, and the
                machine-readable timestamp is worth keeping.
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
            key: 'status',
            header: 'Status',
            width: proportional(2),
            renderCell: (row: TableRow) => (
              /*
                `items-start` is the whole fix for G6's "the Draft badge is
                stretched to the column width". A flex column stretches its
                children on the cross axis by DEFAULT, so the badge grew to the
                column's width and a two-word status became a banner. The
                symptom looked like a Badge problem and was a container one.
              */
              <div data-status-cell className="flex flex-col items-start gap-1">
                <DocumentStatusBadge status={row.status} />
                {row.parse_error_code === null ? null : (
                  <Text as="p" display="block" type="supporting" color="secondary">
                    {PARSE_ERROR_LABELS[row.parse_error_code]}
                    {PARSE_ERROR_ADVICE[row.parse_error_code] === undefined
                      ? null
                      : ` - ${PARSE_ERROR_ADVICE[row.parse_error_code]}`}
                  </Text>
                )}
              </div>
            ),
          },
        ]}
        />
    </div>
  )
}
