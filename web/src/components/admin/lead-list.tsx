'use client'

import Link from 'next/link'
import { endReasonLabel } from '@/lib/admin/leads'
import type { LeadSummaryRow } from '@/lib/admin/leads'

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
export function LeadList({ rows }: { rows: readonly LeadSummaryRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="border border-ink-800 px-5 py-4 text-[13px] text-ink-400">
        No calls have been recorded yet. A lead appears here as soon as a call ends,
        including one that was cut short.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[52rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-ink-800">
            {['Call', 'When', 'Language', 'Projects', 'Score', 'Contact', 'Status'].map(
              (heading) => (
                <th
                  key={heading}
                  className="px-3 py-2 text-[11px] tracking-[0.12em] text-ink-500 uppercase"
                  scope="col"
                >
                  {heading}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-ink-900 align-top">
              <td className="px-3 py-3">
                <Link
                  className="text-[13px] text-ink-100 underline decoration-ink-700 hover:text-brass-400"
                  href={`/admin/leads/${row.id}`}
                >
                  {row.session_id}
                </Link>
                <p className="mt-1 text-[11px] text-ink-500">
                  {endReasonLabel(row.call_end_reason)}
                  {row.ended_cleanly ? null : (
                    <span className="ml-2 border border-warn-500/40 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-ink-300 uppercase">
                      incomplete
                    </span>
                  )}
                </p>
              </td>
              <td className="px-3 py-3 text-[12px] text-ink-400">
                <time dateTime={row.created_at}>{when(row.created_at)}</time>
                <p className="mt-1 text-[11px] text-ink-600">{duration(row)}</p>
              </td>
              <td className="px-3 py-3 text-[12px] text-ink-300 uppercase">{row.language}</td>
              <td className="px-3 py-3 text-[12px] text-ink-300">
                {row.project_ids.length === 0 ? (
                  <span className="text-ink-600">none named</span>
                ) : (
                  row.project_ids.join(', ')
                )}
              </td>
              <td className="px-3 py-3">
                {row.analysis_status === 'failed' ? (
                  // Not a zero: zero reads as an uninterested buyer, and what
                  // happened is that nobody knows yet.
                  <span className="text-[12px] text-warn-500">analysis failed</span>
                ) : row.score_total === null ? (
                  <span className="text-[12px] text-ink-600">pending</span>
                ) : (
                  <span className="text-[15px] text-ink-100">{row.score_total}</span>
                )}
              </td>
              <td className="px-3 py-3 text-[12px]">
                {/* Whether, never what. */}
                {row.contact_present ? (
                  <span className="text-ink-300">captured</span>
                ) : (
                  <span className="text-ink-600">none</span>
                )}
              </td>
              <td className="px-3 py-3">
                <span className="text-[11px] tracking-[0.12em] text-ink-300 uppercase">
                  {row.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
