import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ReactElement } from 'react'
import type { LeadSummaryRow } from '@/lib/admin/leads'

/**
 * Every recordable ending renders as words, including the ones this tier
 * learned about late (task-web-call-end-reason-drift).
 *
 * The bug: `CallEndReason` here listed five of the six the writer can produce,
 * `buyer_farewell_repeated` absent, and both render sites index
 * `END_REASON_LABELS` bare - so React printed `undefined` as nothing and the
 * lead showed a BLANK ending reason. No error, no console warning, just a
 * lead that looked like it ended for no reason.
 *
 * TypeScript could not catch it. `Record<CallEndReason, string>` is satisfied
 * by five keys when the UNION is the wrong copy, so the checker was consistent
 * with itself and wrong about the world. The set parity is guarded in the
 * AGENT suite, where the widen happens; what is guarded here is the thing a
 * reviewer sees.
 *
 * Imported inside each case, matching admin-leads.test.tsx: a RED file that
 * fails to LOAD reports "no tests" and the gate has no case failures to count.
 */
async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

const ROW: LeadSummaryRow = {
  id: 'lead-1',
  session_id: 'sess-1',
  created_at: '2026-09-03T09:00:00Z',
  ended_at: '2026-09-03T09:07:30Z',
  call_end_reason: 'buyer_farewell',
  ended_cleanly: true,
  language: 'en',
  status: 'unreviewed',
  score_total: 61,
  project_ids: ['binghatti-skyrise'],
  contact_present: true,
  analysis_status: 'complete',
}

async function renderList(rows: LeadSummaryRow[]) {
  const { LeadList } = (await load('@/components/admin/lead-list')) as unknown as {
    LeadList: (props: { rows: LeadSummaryRow[] }) => ReactElement
  }
  return render(<LeadList rows={rows} />)
}

afterEach(cleanup)

describe('the ending reason a reviewer reads', () => {
  it('names a call the buyer had to say goodbye to twice', async () => {
    await renderList([{ ...ROW, call_end_reason: 'buyer_farewell_repeated' }])

    // The specific ending #136 made detectable. Before the fix this cell was
    // empty, which reads as "ended for no reason" rather than "ended politely".
    expect(screen.getByText('Buyer said goodbye twice')).toBeTruthy()
  })

  it('shows the raw value rather than nothing for a reason it does not know', async () => {
    // The durable half. A member added to the writer and not yet to this tier
    // must degrade to something a reviewer can read out loud and search for -
    // never to a blank cell. Cast because the point is a value outside the
    // union, which is exactly the case the type system cannot represent.
    const unknown = 'a_reason_from_a_newer_writer' as LeadSummaryRow['call_end_reason']
    await renderList([{ ...ROW, call_end_reason: unknown }])

    expect(screen.getByText('a_reason_from_a_newer_writer')).toBeTruthy()
  })
})
