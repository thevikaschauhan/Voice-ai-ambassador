'use client'

import { useCallback, useState } from 'react'
import {
  END_REASON_LABELS,
  REASON_LABELS,
  SIGNAL_LABELS,
} from '@/lib/admin/leads'
import type { LeadDetailRecord, ReasonCode } from '@/lib/admin/leads'

/**
 * One lead, and the decision a human makes about it.
 *
 * docs/10-: the detail makes model provenance visible. Three things follow from
 * that and none is decoration. The summary is LABELLED generated, because an
 * unlabelled model sentence reads as a fact somebody checked. The score shows
 * its evidence turns, because a number without them is an assertion rather than
 * a finding - and it shows signals that scored NOTHING too, or the total cannot
 * be reconciled with what is on screen. The decision history is shown and never
 * edited, because it is append-only in the database (ADR-020) and a UI that
 * looked editable would be lying about that.
 *
 * The score is guidance. The decision is the human's, which is why the buttons
 * are neutral and the note is free text.
 */

const REASONS: ReasonCode[] = [
  'ready',
  'follow_up',
  'not_interested',
  'invalid_contact',
  'outside_scope',
  'duplicate',
  'other',
]

type Choice = 'qualified' | 'rejected' | null

export function LeadDetail({ lead }: { lead: LeadDetailRecord }) {
  const [choice, setChoice] = useState<Choice>(null)
  const [reason, setReason] = useState<ReasonCode>('ready')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const save = useCallback(async () => {
    if (choice === null) return
    setBusy(true)
    setProblem(null)
    try {
      const response = await fetch(`/api/admin/leads/${lead.id}/decisions`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          new_status: choice,
          reason_code: reason,
          note: note.trim() === '' ? null : note.trim(),
          // The revision the reviewer was LOOKING at. That is what makes the
          // API's optimistic check mean anything: a decision taken against
          // stale data must be refused, not applied.
          revision: lead.revision,
        }),
      })
      if (response.status === 409) {
        // Somebody else decided first. Retrying would overwrite their
        // decision, and decisions are append-only and audited.
        setProblem(
          'Somebody else decided this lead while you were reading it. Reload to see their decision before you add yours.',
        )
        return
      }
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { error?: string }
        setProblem(payload.error ?? 'That decision was not saved.')
        return
      }
      setSaved(true)
    } catch {
      setProblem('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }, [choice, lead.id, lead.revision, note, reason])

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <h1 className="text-[15px] tracking-[0.1em] text-ink-100">{lead.session_id}</h1>
        <p className="text-[12px] text-ink-500">
          {END_REASON_LABELS[lead.call_end_reason]}
          {lead.ended_cleanly ? '' : ' - incomplete'}
          {' · '}
          {lead.language.toUpperCase()}
          {' · '}
          <span className="uppercase">{lead.status}</span>
        </p>
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
          Summary
          {/* The label is not a footnote: it is the difference between a
              sentence a person wrote and one a model produced. */}
          <span className="ml-2 border border-ink-700 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-ink-400">
            model-generated
          </span>
        </h2>
        {lead.summary === null ? (
          <p className="text-[13px] text-ink-500">
            {lead.analysis_status === 'failed'
              ? 'Analysis failed for this call, so there is no summary. The call itself is saved.'
              : 'No summary yet.'}
          </p>
        ) : (
          <p className="max-w-[80ch] text-[13px] leading-relaxed text-ink-200">{lead.summary}</p>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
          Interest score
          {lead.score !== null ? (
            <span className="ml-2 text-ink-600">rubric {lead.score.score_version}</span>
          ) : null}
        </h2>
        {lead.score === null ? (
          <p className="text-[13px] text-ink-500">No score: the analysis has not completed.</p>
        ) : (
          <>
            <p className="text-[24px] leading-none text-ink-100">{lead.score.total}</p>
            <ol className="flex flex-col gap-2">
              {lead.score.breakdown.map((item) => (
                <li
                  key={item.signal}
                  className="flex flex-wrap items-baseline gap-x-4 border-b border-ink-900 pb-2 text-[12px]"
                >
                  <span className="min-w-[16rem] text-ink-200">{SIGNAL_LABELS[item.signal]}</span>
                  <span className="text-ink-100">{item.points_awarded}</span>
                  <span className="text-ink-600">of {item.max_points}</span>
                  {item.observed ? (
                    <span className="text-ink-500">
                      {item.evidence_turn_indexes.length === 0
                        ? 'no cited turn'
                        : item.evidence_turn_indexes.map((index) => `turn ${index}`).join(', ')}
                    </span>
                  ) : (
                    // Shown rather than omitted: a total that cannot be
                    // reconciled with the rows above it is not evidence.
                    <span className="text-ink-600">not observed</span>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">Contact</h2>
        <p className="text-[13px] text-ink-200">
          {lead.contact.status === 'captured'
            ? [lead.contact.name, lead.contact.phone, lead.contact.email]
                .filter((value) => value !== null && value !== '')
                .join(' · ')
            : `Not captured (${lead.contact.status.replace('_', ' ')})`}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
          Buyer turns cited by the score
        </h2>
        <ol className="flex flex-col gap-2">
          {lead.turns.map((turn) => (
            <li key={turn.turn_index} className="text-[12px] leading-relaxed">
              <span className="mr-2 text-ink-600">turn {turn.turn_index}</span>
              <span className="text-ink-200">{turn.text}</span>
              {turn.audit_incomplete ? (
                <span className="ml-2 text-[10px] tracking-[0.1em] text-warn-500 uppercase">
                  incomplete
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">Decisions</h2>
        {lead.decisions.length === 0 ? (
          <p className="text-[13px] text-ink-500">No decision has been recorded yet.</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {lead.decisions.map((decision) => (
              <li key={decision.id} className="text-[12px] text-ink-300">
                <span className="text-ink-500">#{decision.sequence}</span>{' '}
                <span className="uppercase">{decision.new_status}</span>{' '}
                <span className="text-ink-500">{REASON_LABELS[decision.reason_code]}</span>
                {decision.note === null ? null : (
                  <span className="text-ink-400"> - {decision.note}</span>
                )}
                <time className="ml-2 text-ink-600" dateTime={decision.decided_at}>
                  {decision.decided_at.slice(0, 16).replace('T', ' ')}
                </time>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="flex flex-col gap-3 border-t border-ink-800 pt-5">
        <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
          Your decision
        </h2>
        <p className="max-w-[74ch] text-[12px] leading-relaxed text-ink-500">
          The score is guidance. Qualifying or rejecting is your call, it is recorded
          against this revision of the lead, and it cannot be edited afterwards.
        </p>

        {saved ? (
          <p className="border border-ink-700 px-5 py-3.5 text-[13px] text-ink-300" role="status">
            Decision saved. Reload to see it in the history above.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-3">
              {(
                [
                  ['qualified', 'Qualify'],
                  ['rejected', 'Reject'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setChoice(value)}
                  className={`border px-5 py-2.5 text-[13px] tracking-wide ${
                    choice === value
                      ? 'border-brass-500 text-brass-400'
                      : 'border-ink-600 text-ink-100 hover:border-brass-500'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
                htmlFor="decision-reason"
              >
                Reason
              </label>
              <select
                id="decision-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value as ReasonCode)}
                className="w-[24ch] border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
              >
                {REASONS.map((code) => (
                  <option key={code} value={code}>
                    {REASON_LABELS[code]}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
                htmlFor="decision-note"
              >
                Note
              </label>
              <textarea
                id="decision-note"
                rows={3}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                className="max-w-[60ch] border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
              />
            </div>

            <button
              type="button"
              disabled={choice === null || busy}
              onClick={() => void save()}
              className="w-fit border border-ink-600 px-5 py-2.5 text-[13px] tracking-wide text-ink-100 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
            >
              {busy ? 'Saving' : 'Save decision'}
            </button>
          </>
        )}

        {problem !== null ? (
          <p
            className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300"
            role="status"
          >
            {problem}
          </p>
        ) : null}
      </section>
    </div>
  )
}
