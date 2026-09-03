'use client'

import Link from 'next/link'
import { PARSE_ERROR_ADVICE, PARSE_ERROR_LABELS } from '@/lib/admin/knowledge'
import type { DocumentRow } from '@/lib/admin/knowledge'

/**
 * The documents the ambassador may draw on, with their status.
 *
 * A failed parse is shown with WHAT failed and WHAT TO DO about it, because
 * `docs/10-` step 2 makes that the point of the status: a scanned PDF ends as
 * `no_extractable_text` and the admin has to be told that scans need OCR and
 * that OCR is deferred. A bare "failed" sends somebody to re-upload the same
 * file.
 */
export function DocumentList({ rows }: { rows: readonly DocumentRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="border border-ink-800 px-5 py-4 text-[13px] text-ink-400">
        No documents yet. Paste a paragraph or upload a PDF, DOCX or TXT to start.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-ink-800">
            {['Document', 'Source', 'Added', 'Status'].map((heading) => (
              <th
                key={heading}
                className="px-3 py-2 text-[11px] tracking-[0.12em] text-ink-500 uppercase"
                scope="col"
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.id}-${row.revision}`} className="border-b border-ink-900 align-top">
              <td className="px-3 py-3">
                <Link
                  className="text-[13px] text-ink-100 underline decoration-ink-700 hover:text-brass-400"
                  href={`/admin/knowledge/${row.id}`}
                >
                  {row.title}
                </Link>
                <p className="mt-1 text-[11px] text-ink-600">revision {row.revision}</p>
              </td>
              <td className="px-3 py-3 text-[12px] text-ink-400 uppercase">{row.source_type}</td>
              <td className="px-3 py-3 text-[12px] text-ink-400">
                <time dateTime={row.created_at}>
                  {row.created_at.slice(0, 16).replace('T', ' ')}
                </time>
              </td>
              <td className="px-3 py-3">
                <span
                  className={`text-[12px] ${
                    row.status === 'failed'
                      ? 'text-warn-500'
                      : row.status === 'published'
                        ? 'text-brass-400'
                        : 'text-ink-300'
                  }`}
                >
                  {row.status}
                </span>
                {row.parse_error_code === null ? null : (
                  <p className="mt-1 max-w-[46ch] text-[11px] leading-relaxed text-ink-500">
                    {PARSE_ERROR_LABELS[row.parse_error_code]}
                    {PARSE_ERROR_ADVICE[row.parse_error_code] === undefined
                      ? null
                      : ` - ${PARSE_ERROR_ADVICE[row.parse_error_code]}`}
                  </p>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
