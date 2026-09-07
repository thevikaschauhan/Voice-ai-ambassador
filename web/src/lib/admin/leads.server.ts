import 'server-only'

import { readForPage } from '@/lib/admin/read'
import type { PageRead } from '@/lib/admin/read'
import type {
  ContactStatus,
  LeadDetailRecord,
  LeadStatus,
  LeadSummaryRow,
  ScoreItem,
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

/**
 * The four fields the DETAIL sends in a shape this tier does not use.
 *
 * Verified against a CAPTURED `GET /v1/leads/<id>` response, not against
 * `admin_api.py` read hopefully: see `admin-real-shapes.test.ts`, whose
 * fixture is that response byte for byte. Each of these was a live defect
 * (task-web-lead-detail-score), and the last one was a 500 on the whole page.
 */

/**
 * The score, assembled from the three flat fields the API sends.
 *
 * There is no `score` object in the response - `admin_api.py` never builds
 * one - so reading `upstream.score` returned undefined for every lead and the
 * detail said "the analysis has not completed" for completed analyses. The
 * API contract stays as it is (three fields, already consumed by the list and
 * documented in docs/10); the view shape is assembled here, which is what this
 * layer is for.
 */
function toScore(upstream: Record<string, unknown>): LeadDetailRecord['score'] {
  const total = upstream.score_total
  const version = upstream.score_version
  // A score without a total is not a score. `analysis_status` is not consulted
  // on purpose: the fields carry their own answer, and a lead whose analysis
  // failed has a null total anyway.
  if (typeof total !== 'number' || typeof version !== 'string') return null
  return {
    total,
    score_version: version,
    breakdown: toBreakdown(upstream.score_breakdown),
  }
}

/**
 * The breakdown, which arrives as a JSON STRING.
 *
 * `get_lead` does `SELECT *` and asyncpg hands jsonb back as text; the
 * repository calls `json.loads` by hand for `chunk_refs` and nowhere else. An
 * array is accepted too, so the day a codec is registered upstream this keeps
 * working - but the string is what it sends today, and the fixture pins that.
 *
 * A breakdown that will not parse yields an EMPTY list rather than throwing:
 * losing the evidence rows is bad, and taking the whole page down with them is
 * worse. That was this defect's own lesson.
 */
function toBreakdown(value: unknown): ScoreItem[] {
  if (Array.isArray(value)) return value as ScoreItem[]
  if (typeof value !== 'string' || value === '') return []
  try {
    const parsed: unknown = JSON.parse(value)
    return Array.isArray(parsed) ? (parsed as ScoreItem[]) : []
  } catch {
    return []
  }
}

/**
 * A turn, whose `speaker` and `text` live INSIDE the `payload` string.
 *
 * The API seals the turn model as JSON and sends the opened string; there is no
 * `speaker` and no `text` on the turn itself, which is where `LeadTurnView`
 * looked. So every cited buyer turn rendered as "turn 4" with nothing after it.
 * `audit_incomplete` stays on the turn - it is a column, not part of the
 * sealed payload - and a payload that will not parse keeps the turn with empty
 * text, because a turn the score cited and nobody can read is a fact worth
 * seeing.
 */
function toTurns(value: unknown): LeadDetailRecord['turns'] {
  if (!Array.isArray(value)) return []
  return value.map((turn) => {
    const row = turn as Record<string, unknown>
    const payload = parsePayload(row.payload)
    return {
      turn_index: Number(row.turn_index ?? 0),
      speaker: payload.speaker === 'agent' ? 'agent' : 'buyer',
      text: typeof payload.text === 'string' ? payload.text : '',
      audit_incomplete: row.audit_incomplete === true,
    }
  })
}

function parsePayload(value: unknown): { speaker?: unknown; text?: unknown } {
  if (value !== null && typeof value === 'object') return value as { speaker?: unknown }
  if (typeof value !== 'string' || value === '') return {}
  try {
    const parsed: unknown = JSON.parse(value)
    return parsed !== null && typeof parsed === 'object' ? (parsed as { text?: unknown }) : {}
  } catch {
    return {}
  }
}

/**
 * A decision, whose timestamp the API calls `created_at`.
 *
 * `decided_at` existed only in this tier's own type, so it was undefined, and
 * the render's `decision.decided_at.slice(0, 16)` made that a TypeError: the
 * detail route returned 500 for any lead that had ever been qualified or
 * rejected. Not a missing timestamp - an unreachable page.
 */
function toDecisions(value: unknown): LeadDetailRecord['decisions'] {
  if (!Array.isArray(value)) return []
  return value.map((decision) => {
    const row = decision as Record<string, unknown>
    return {
      id: String(row.id ?? ''),
      sequence: Number(row.sequence ?? 0),
      previous_status: row.previous_status as LeadStatus,
      new_status: row.new_status as LeadDetailRecord['decisions'][number]['new_status'],
      reason_code: row.reason_code as LeadDetailRecord['decisions'][number]['reason_code'],
      note: typeof row.note === 'string' ? row.note : null,
      decided_at: String(row.created_at ?? ''),
    }
  })
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
      score: toScore(upstream),
      contact: (upstream.contact as LeadDetailRecord['contact']) ?? {
        status: 'not_asked',
        name: null,
        phone: null,
        email: null,
      },
      turns: toTurns(upstream.turns),
      decisions: toDecisions(upstream.decisions),
    },
  }
}
