/**
 * The lead shapes this surface renders, from `docs/02-`'s Phase 2 lead record.
 *
 * Declared here rather than inferred from a fetch, because these are a
 * CONTRACT with the Python admin API (ADR-021) and the point of writing them
 * down is that drift becomes a type error rather than an empty column. The
 * Pydantic models are the executable source of truth; if these diverge, the
 * divergence gets reported to the API's owner and fixed in one change, not
 * absorbed here.
 *
 * Client-safe: types only, no secret and no upstream address. The proxy is
 * what talks to the API.
 */

export type LeadStatus = 'unreviewed' | 'qualified' | 'rejected'

/**
 * Mirrors `CallEndReason` in agent/src/ambassador/schemas.py, in its order.
 *
 * This copy listed five of six until 2026-09-04 - `buyer_farewell_repeated`
 * absent - and TypeScript could not see it, because `Record<CallEndReason,
 * string>` below is satisfied by five keys when the union itself is the wrong
 * copy. The parity guard lives in the agent suite (tests/test_call_end_reason.py),
 * where the widening happens, because both times this drifted the writer was
 * changed first and the reader was what nobody remembered.
 */
export type CallEndReason =
  | 'buyer_farewell'
  | 'buyer_farewell_repeated'
  | 'agent_farewell'
  | 'duration_cap'
  | 'buyer_left'
  | 'session_error'

/**
 * Mirrors `ContactStatus` in agent/src/ambassador/schemas.py.
 *
 * The list does not show these four - docs/10-:316 asks it for contact-present,
 * one bit - but `leads.server.ts` DERIVES that bit by comparing the API's
 * `contact_status` against `'captured'`, and that comparison was written
 * against a field typed `string`. Nothing checked the literal, so renaming this
 * member upstream would have left the compiler happy while every lead silently
 * read as having no contact. Typing the set is what makes the comparison a
 * checked one; the parity guard is in the agent suite, where the widen happens
 * (tests/test_call_end_reason.py).
 */
export type ContactStatus = 'not_asked' | 'captured' | 'declined' | 'unconfirmed'

export type AnalysisStatus = 'pending' | 'complete' | 'failed'

export type ScoreSignal =
  | 'budget_stated'
  | 'project_named'
  | 'timeline_stated'
  | 'contact_shared'
  | 'viewing_or_human_requested'
  | 'questions_asked'
  | 'call_length'

export type ReasonCode =
  | 'ready'
  | 'follow_up'
  | 'not_interested'
  | 'invalid_contact'
  | 'outside_scope'
  | 'duplicate'
  | 'other'

/**
 * The list row: OPERATIONAL FIELDS ONLY.
 *
 * docs/10- draws this line and it is worth restating where the type lives: no
 * buyer words, no contact VALUES - only whether a contact exists. A transcript
 * line on a list is a transcript nobody chose to open, and a phone number on a
 * list is a phone number in a screenshot.
 */
export interface LeadSummaryRow {
  id: string
  session_id: string
  created_at: string
  ended_at: string | null
  call_end_reason: CallEndReason
  ended_cleanly: boolean
  language: string
  status: LeadStatus
  score_total: number | null
  project_ids: string[]
  contact_present: boolean
  analysis_status: AnalysisStatus
}

export interface ScoreItem {
  signal: ScoreSignal
  observed: boolean
  raw_value: number | boolean
  points_awarded: number
  max_points: number
  evidence_turn_indexes: number[]
}

export interface InterestScore {
  total: number
  score_version: string
  breakdown: ScoreItem[]
}

export interface LeadTurnView {
  turn_index: number
  speaker: 'buyer' | 'agent'
  text: string
  audit_incomplete: boolean
}

export interface ContactView {
  status: 'not_asked' | 'captured' | 'declined' | 'unconfirmed'
  name: string | null
  phone: string | null
  email: string | null
}

export interface DecisionView {
  id: string
  sequence: number
  previous_status: LeadStatus
  new_status: Exclude<LeadStatus, 'unreviewed'>
  reason_code: ReasonCode
  note: string | null
  decided_at: string
}

export interface LeadDetailRecord extends LeadSummaryRow {
  /** The optimistic-concurrency counter the reviewer's decision must carry. */
  revision: number
  /** Model-generated, and labelled as such wherever it is shown. */
  summary: string | null
  score: InterestScore | null
  contact: ContactView
  turns: LeadTurnView[]
  decisions: DecisionView[]
}

/** How a signal reads on screen. The API sends the enum; this is the English. */
export const SIGNAL_LABELS: Record<ScoreSignal, string> = {
  budget_stated: 'Budget stated',
  project_named: 'Project named',
  timeline_stated: 'Timeline stated',
  contact_shared: 'Contact shared',
  viewing_or_human_requested: 'Viewing or human requested',
  questions_asked: 'Questions asked',
  call_length: 'Call length',
}

export const REASON_LABELS: Record<ReasonCode, string> = {
  ready: 'Ready',
  follow_up: 'Follow up',
  not_interested: 'Not interested',
  invalid_contact: 'Invalid contact',
  outside_scope: 'Outside scope',
  duplicate: 'Duplicate',
  other: 'Other',
}

/** Why a call ended, in words a reviewer can act on. */
export const END_REASON_LABELS: Record<CallEndReason, string> = {
  buyer_farewell: 'Buyer said goodbye',
  // Their first goodbye was not clean enough for the strict rule and the
  // repeat is what ended the call (agent.py, the repeated-closing path). Said
  // plainly, because "twice" is the operational detail: the ending was polite,
  // and the buyer had to ask for it more than once.
  buyer_farewell_repeated: 'Buyer said goodbye twice',
  agent_farewell: 'Ambassador closed the call',
  duration_cap: 'Hit the duration cap',
  buyer_left: 'Buyer left',
  session_error: 'Session error',
}

/**
 * The label to show, and never nothing.
 *
 * Both render sites used to index `END_REASON_LABELS` directly, so a reason
 * this tier had not been told about printed as `undefined` - which React
 * renders as an empty cell. A reviewer saw a lead that looked like it ended
 * for no reason, with no error anywhere to explain it.
 *
 * Falling back to the raw value keeps the failure legible and searchable: the
 * reviewer can read it out loud and grep for it, which is what turns "the UI
 * looks broken" into "the writer sends a reason we do not label yet". The
 * parameter is widened to `string` deliberately - the whole point is a value
 * from outside the union, which is the case the type cannot express.
 */
export function endReasonLabel(reason: string): string {
  return END_REASON_LABELS[reason as CallEndReason] ?? reason
}
