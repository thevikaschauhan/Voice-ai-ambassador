/**
 * The knowledge shapes this surface renders, from `docs/02-`'s Phase 2
 * knowledge contracts.
 *
 * THE NAMES ARE NOT THIS FILE'S TO CHOOSE. `retrieval_scope`,
 * `conflict_code` and the review actions are copied from
 * `ambassador/knowledge.py`, which owns the closure; a UI that invents its own
 * word for `inventory_governed` disagrees with the thing it is displaying. The
 * English labels below are presentation, and each one sits beside the enum it
 * renders so a rename shows up as a type error rather than a stale caption.
 */

/** The four scopes, in the closure's own order (`knowledge.py`). */
export const RETRIEVAL_SCOPES = [
  'admin_only',
  'general_knowledge',
  'project_knowledge',
  'inventory_governed',
] as const

export type RetrievalScope = (typeof RETRIEVAL_SCOPES)[number]

export type ConflictCode = 'conflicts_with_inventory' | 'unknown_project'

export type FigureKind = 'amount' | 'percent' | 'year' | 'count'

export type DocumentStatus = 'parsing' | 'draft' | 'published' | 'failed' | 'archived'

export type ParseErrorCode =
  | 'unsupported_type'
  | 'invalid_encoding'
  | 'limit_exceeded'
  | 'no_extractable_text'
  | 'malformed'

export type SourceType = 'pdf' | 'docx' | 'txt' | 'paste'

export interface DocumentRow {
  id: string
  revision: number
  title: string
  source_type: SourceType
  status: DocumentStatus
  parse_error_code: ParseErrorCode | null
  created_at: string
  published_at: string | null
}

/**
 * One extracted occurrence.
 *
 * `active_approval_id` is the whole review: null means nobody has approved
 * THIS occurrence, and an occurrence is not a value - the same figure written
 * twice is two rows, and approving one says nothing about the other.
 */
export interface KnowledgeFigureView {
  id: string
  value: string
  kind: FigureKind
  currency: string | null
  unit: string | null
  surface: string
  source_sentence: string
  page: number | null
  active_approval_id: string | null
}

export interface KnowledgeChunkView {
  id: string
  ordinal: number
  heading: string | null
  body: string
  retrieval_scope: RetrievalScope
  project_id: string | null
  conflict_code: ConflictCode | null
  page_start: number | null
  page_end: number | null
  figures: KnowledgeFigureView[]
}

export interface DocumentDetail extends DocumentRow {
  chunks: KnowledgeChunkView[]
}

export const SCOPE_LABELS: Record<RetrievalScope, string> = {
  admin_only: 'Admin only',
  general_knowledge: 'General knowledge',
  project_knowledge: 'Project knowledge',
  inventory_governed: 'Inventory governed',
}

/**
 * What each scope MEANS for a call, which is the part a reviewer is deciding.
 *
 * Written from `knowledge.py`'s own reasoning rather than paraphrased loosely:
 * two of the four are permanently closed to the model, and a reviewer choosing
 * between them should be told that here rather than discovering it later.
 */
export const SCOPE_EFFECTS: Record<RetrievalScope, string> = {
  admin_only: 'Never reaches a call. This is the default, because a document uploaded and forgotten must not be reachable.',
  general_knowledge: 'Process and FAQ material. Always eligible for retrieval once published.',
  project_knowledge: 'Descriptive prose bound to one project. Ranked first when that project is in play; needs a project id before it can be published.',
  inventory_governed: 'Prices, sizes, plans, handover, status and unit types come from data/inventory.json. Permanently closed to a call - a brochure does not get to restate them.',
}

export const CONFLICT_LABELS: Record<ConflictCode, string> = {
  conflicts_with_inventory: 'Conflicts with inventory',
  unknown_project: 'Unknown project',
}

export const CONFLICT_EFFECTS: Record<ConflictCode, string> = {
  conflicts_with_inventory:
    'A structured inventory field says something different, so this stays admin-only whatever scope is asked for, until the inventory is corrected through its own review.',
  unknown_project:
    'This is bound to a project that is not in inventory, so it is not publishable: prose about a tower we do not sell is prose nobody can check.',
}

export const PARSE_ERROR_LABELS: Record<ParseErrorCode, string> = {
  unsupported_type: 'Unsupported file type',
  invalid_encoding: 'Invalid encoding - the text is not valid UTF-8',
  limit_exceeded: 'Too large',
  no_extractable_text: 'No extractable text',
  malformed: 'Malformed file',
}

/** What the admin should do about a parse failure, where there is something to do. */
export const PARSE_ERROR_ADVICE: Partial<Record<ParseErrorCode, string>> = {
  no_extractable_text:
    'This looks like a scan. OCR is deferred, so it needs a text-bearing copy of the document instead.',
  limit_exceeded: 'Split it, or paste the section that matters.',
  unsupported_type: 'Only PDF, DOCX and TXT are accepted.',
}

/**
 * The byte cap the browser enforces before uploading.
 *
 * The API caps this too and that is the real gate; refusing here saves a
 * reviewer waiting for a 12MB upload to be told no, and lets the limit be
 * stated out loud next to the control.
 */
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024

export const ACCEPTED_UPLOAD_EXTENSIONS = '.pdf,.docx,.txt'

/** Only these two are ever prompt material (`knowledge.py`'s _PROMPT_ELIGIBLE). */
export function scopeCanReachACall(scope: RetrievalScope): boolean {
  return scope === 'general_knowledge' || scope === 'project_knowledge'
}
