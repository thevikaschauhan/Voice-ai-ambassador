import 'server-only'

import { readForPage } from '@/lib/admin/read'
import type { PageRead } from '@/lib/admin/read'
import type {
  ContactStatus,
  LeadDetailRecord,
  LeadStatus,
  LeadSummaryRow,
} from '@/lib/admin/leads'

/**
 * Reading leads from the admin API, in the shapes it actually sends.
 *
 * This layer exists because the API's response is not this tier's model, and
 * pretending otherwise is what put empty lists on the deployed page. Two
 * differences are handled here, both verified against
 * `adapter/repository.py`:
 *
 *   the list is a BARE ARRAY, not an envelope
 *   it carries `contact_status`, not `contact_present`
 *
 * The status is typed as `ContactStatus` rather than `string`, so the
 * derivation below compares against a member the compiler knows about.
 *
 * `contact_present` is DERIVED rather than requested. docs/10- asks the list to
 * show contact-present; the API sends the status, which is strictly more
 * information, so asking its owner to add a boolean already implied by a column
 * he has would be asking for redundancy.
 */

/** The row as the repository names its columns (`list_leads`). */
interface UpstreamLeadRow {
  id: string
  session_id: string
  created_at: string
  ended_at: string | null
  call_end_reason: LeadSummaryRow['call_end_reason']
  ended_cleanly: boolean
  language: string
  status: LeadStatus
  score_total: number | null
  analysis_status: LeadSummaryRow['analysis_status']
  /**
   * Typed as the union rather than `string`, so `toRow`'s `=== 'captured'`
   * below is a comparison the compiler checks. As a bare `string` it was a
   * magic word: the day that member is renamed upstream, an unchecked
   * comparison keeps compiling and quietly reports every lead as having no
   * contact - a plausible wrong answer, which is harder to notice than a
   * blank one.
   */
  contact_status?: ContactStatus | null
  /**
   * Not in the list projection yet: docs/10-:315 names project ids as a list
   * field and `list_leads` does not select them (reported as drift item 6,
   * toby's `task-p2-admin-list-filters`). Optional here so the column populates
   * the day his lands, with no change on this side.
   */
  project_ids?: string[] | null
}

function toRow(row: UpstreamLeadRow): LeadSummaryRow {
  return {
    id: row.id,
    session_id: row.session_id,
    created_at: row.created_at,
    ended_at: row.ended_at,
    call_end_reason: row.call_end_reason,
    ended_cleanly: row.ended_cleanly,
    language: row.language,
    status: row.status,
    score_total: row.score_total,
    analysis_status: row.analysis_status,
    project_ids: row.project_ids ?? [],
    contact_present: row.contact_status === 'captured',
  }
}

export async function readLeadRows(request: Request): Promise<PageRead<LeadSummaryRow[]>> {
  const search = new URL(request.url).search
  const read = await readForPage<UpstreamLeadRow[]>(request.headers.get('cookie'), {
    route: 'leads',
    search,
  })
  if (read.state !== 'ok') return read
  return { state: 'ok', data: read.data.map(toRow) }
}

export async function readLead(
  request: Request,
  id: string,
): Promise<PageRead<LeadDetailRecord>> {
  const read = await readForPage<UpstreamLeadRow & Record<string, unknown>>(
    request.headers.get('cookie'),
    { route: 'lead', id },
  )
  if (read.state !== 'ok') return read
  const upstream = read.data
  return {
    state: 'ok',
    data: {
      ...toRow(upstream),
      revision: Number(upstream.revision ?? 0),
      summary: (upstream.summary as string | null) ?? null,
      score: (upstream.score as LeadDetailRecord['score']) ?? null,
      contact: (upstream.contact as LeadDetailRecord['contact']) ?? {
        status: 'not_asked',
        name: null,
        phone: null,
        email: null,
      },
      turns: (upstream.turns as LeadDetailRecord['turns']) ?? [],
      decisions: (upstream.decisions as LeadDetailRecord['decisions']) ?? [],
    },
  }
}
