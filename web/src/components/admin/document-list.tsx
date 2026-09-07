'use client'

import Link from 'next/link'
import { Badge } from '@astryxdesign/core/Badge'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Table, proportional } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import { PARSE_ERROR_ADVICE, PARSE_ERROR_LABELS } from '@/lib/admin/knowledge'
import type { DocumentRow, DocumentStatus } from '@/lib/admin/knowledge'

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

/**
 * All five statuses, each with its own word and weight.
 *
 * TYPED AS Record<DocumentStatus, ...> ON PURPOSE: a sixth status added to the
 * enum now fails the build here instead of quietly rendering in the neutral
 * style, which is how `parsing` and `archived` came to look exactly like a
 * draft ready to publish.
 */
const STATUS: Record<
  DocumentStatus,
  { label: string; variant: 'neutral' | 'info' | 'success' | 'error' }
> = {
  parsing: { label: 'Parsing', variant: 'info' },
  draft: { label: 'Draft', variant: 'neutral' },
  published: { label: 'Published', variant: 'success' },
  failed: { label: 'Failed', variant: 'error' },
  archived: { label: 'Archived', variant: 'neutral' },
}

export function DocumentList({ rows }: { rows: readonly DocumentRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No documents yet."
        description="Paste a paragraph or upload a PDF, DOCX or TXT to start."
      />
    )
  }

  return (
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
            // Uppercased in the STRING, not by a CSS text-transform: the cell
            // used to carry "pdf" in the DOM and show "PDF" on screen, the
            // same mismatch the status badge fixes.
            <Text as="span">{row.source_type.toUpperCase()}</Text>
          ),
        },
        {
          key: 'created_at',
          header: 'Added',
          width: proportional(1),
          renderCell: (row: TableRow) => (
            // A native <time>: Text's `as` has no time tag, and the
            // machine-readable timestamp is worth keeping.
            <time className="text-[12px]" dateTime={row.created_at}>
              {row.created_at.slice(0, 16).replace('T', ' ')}
            </time>
          ),
        },
        {
          key: 'status',
          header: 'Status',
          width: proportional(2),
          renderCell: (row: TableRow) => (
            <div className="flex flex-col gap-1">
              <Badge variant={STATUS[row.status].variant} label={STATUS[row.status].label} />
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
  )
}
