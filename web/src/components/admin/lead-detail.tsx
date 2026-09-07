'use client'

import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { Badge } from '@astryxdesign/core/Badge'
import { Card } from '@astryxdesign/core/Card'
import { Field } from '@astryxdesign/core/Field'
import { Text } from '@astryxdesign/core/Text'
import { TextArea } from '@astryxdesign/core/TextArea'
import { ToggleButton } from '@astryxdesign/core/ToggleButton'
import { LeadStatusBadge } from './status-badge'
import {
  endReasonLabel,
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

/**
 * One card, one h2. The heading level is fixed at 2 on purpose: the page h1 is
 * the shell's, so every section here is a child of it - and this component no
 * longer renders a heading of its own, which is what stopped
 * /admin/leads/<id> shipping two level-one headings.
 */
function Section({
  heading,
  aside,
  children,
}: {
  heading: string
  /** A qualifier that belongs to the heading, like the rubric version. */
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <Card padding={4}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-2">
          {/* `large` + semibold: Astryx's TextType has no 'title' - the scale
              is body/large/label/supporting/code/display-1..3/inherit, and
              display-3 is the page h1's size. */}
          <Text as="h2" type="large" weight="semibold">
            {heading}
          </Text>
          {aside}
        </div>
        {children}
      </div>
    </Card>
  )
}

export function LeadDetail({ lead }: { lead: LeadDetailRecord }) {
  const [choice, setChoice] = useState<Choice>(null)
  const [reason, setReason] = useState<ReasonCode>('ready')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const save = useCallback(async () => {
    // Answered rather than pre-empted by a disabled button: qualify or reject
    // is a choice the admin can make, so the press asks for it. The early
    // return stays - it is what keeps a decision off the lead - it just says
    // why now.
    if (choice === null) {
      setSaved(false)
      setProblem('Choose qualify or reject.')
      return
    }
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
          // The revision the reviewer was LOOKING at, under the name the API
          // validates: `admin_api.DecisionRequest` declares
          // `expected_lead_revision`, and docs/02- names it that too. Sending
          // `revision` 422'd, which reads as a haunted failure rather than a
          // field name - and the proxy could not catch it, because forwarding a
          // body verbatim is its job.
          expected_lead_revision: lead.revision,
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
    <div className="flex flex-col gap-6">
      {/*
        No h1 here. The session id is the page heading and AdminAppShell
        renders it, so this component adding one gave /admin/leads/<id> two
        level-one headings.
      */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Text as="span" type="supporting" color="secondary">
          {endReasonLabel(lead.call_end_reason)}
          {lead.ended_cleanly ? '' : ' - incomplete'}
          {' · '}
          {lead.language.toUpperCase()}
        </Text>
        <LeadStatusBadge status={lead.status} />
      </div>

      <Section
        heading="Summary"
        /* The label is not a footnote: it is the difference between a sentence
           a person wrote and one a model produced. A neutral badge rather than
           a coloured one - it is a provenance fact, not a problem. */
        aside={<Badge variant="neutral" label="model-generated" />}
      >
        {lead.summary === null ? (
          <Text as="p" display="block" color="secondary">
            {lead.analysis_status === 'failed'
              ? 'Analysis failed for this call, so there is no summary. The call itself is saved.'
              : 'No summary yet.'}
          </Text>
        ) : (
          <Text as="p" display="block">
            {lead.summary}
          </Text>
        )}
      </Section>

      <Section
        heading="Interest score"
        aside={
          lead.score !== null ? (
            <Text as="span" type="supporting" color="secondary">
              rubric {lead.score.score_version}
            </Text>
          ) : null
        }
      >
        {lead.score === null ? (
          <Text as="p" display="block" color="secondary">
            No score: the analysis has not completed.
          </Text>
        ) : (
          <>
            <Text as="p" display="block" type="display-2">
              {lead.score.total}
            </Text>
            <ol className="flex flex-col gap-2">
              {lead.score.breakdown.map((item) => (
                <li
                  key={item.signal}
                  className="flex flex-wrap items-baseline gap-x-4 border-b border-current/10 pb-2"
                >
                  <span className="inline-block min-w-[16rem]">
                    <Text as="span">{SIGNAL_LABELS[item.signal]}</Text>
                  </span>
                  <Text as="span">{item.points_awarded}</Text>
                  <Text as="span" color="secondary">
                    of {item.max_points}
                  </Text>
                  {item.observed ? (
                    <Text as="span" color="secondary">
                      {item.evidence_turn_indexes.length === 0
                        ? 'no cited turn'
                        : item.evidence_turn_indexes.map((index) => `turn ${index}`).join(', ')}
                    </Text>
                  ) : (
                    // Shown rather than omitted: a total that cannot be
                    // reconciled with the rows above it is not evidence.
                    <Text as="span" color="secondary">
                      not observed
                    </Text>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </Section>

      <Section heading="Contact">
        <Text as="p" display="block">
          {lead.contact.status === 'captured'
            ? [lead.contact.name, lead.contact.phone, lead.contact.email]
                .filter((value) => value !== null && value !== '')
                .join(' · ')
            : `Not captured (${lead.contact.status.replace('_', ' ')})`}
        </Text>
      </Section>

      <Section heading="Buyer turns cited by the score">
        <ol className="flex flex-col gap-2">
          {lead.turns.map((turn) => (
            <li key={turn.turn_index} className="flex flex-wrap items-baseline gap-2">
              <Text as="span" type="supporting" color="secondary">
                turn {turn.turn_index}
              </Text>
              <Text as="span">{turn.text}</Text>
              {turn.audit_incomplete ? <Badge variant="warning" label="incomplete" /> : null}
            </li>
          ))}
        </ol>
      </Section>

      <Section heading="Decisions">
        {lead.decisions.length === 0 ? (
          <Text as="p" display="block" color="secondary">
            No decision has been recorded yet.
          </Text>
        ) : (
          <ol className="flex flex-col gap-2">
            {lead.decisions.map((decision) => (
              /*
                Append-only in the database (ADR-020), so there is nothing
                interactive in here and a case asserts that: a row that looked
                editable would be lying about the contract.
              */
              <li key={decision.id} className="flex flex-wrap items-baseline gap-2">
                <Text as="span" type="supporting" color="secondary">
                  #{decision.sequence}
                </Text>
                <LeadStatusBadge status={decision.new_status} />
                <Text as="span" color="secondary">
                  {REASON_LABELS[decision.reason_code]}
                </Text>
                {decision.note === null ? null : <Text as="span">- {decision.note}</Text>}
                <time className="text-[12px] opacity-70" dateTime={decision.decided_at}>
                  {decision.decided_at.slice(0, 16).replace('T', ' ')}
                </time>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section heading="Your decision">
        <Text as="p" display="block" color="secondary">
          The score is guidance. Qualifying or rejecting is your call, it is recorded
          against this revision of the lead, and it cannot be edited afterwards.
        </Text>

        {saved ? (
          <p role="status">
            <Text as="span">Decision saved. Reload to see it in the history above.</Text>
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-3">
              {(
                [
                  ['qualified', 'Qualify'],
                  ['rejected', 'Reject'],
                ] as const
              ).map(([value, label]) => (
                /*
                  A ToggleButton, so the choice is IN THE ACCESSIBILITY TREE:
                  it keeps role=button - the cases press it by that role - and
                  adds aria-pressed, which two tinted borders never carried.
                  Before this, a screen reader user could not tell which
                  decision they were about to save.

                  Exclusive by construction: setting the choice un-presses the
                  other one, and pressing the pressed one clears it rather than
                  leaving a decision selected that the reviewer tried to undo.
                */
                <ToggleButton
                  key={value}
                  label={label}
                  isPressed={choice === value}
                  onPressedChange={(isPressed) => setChoice(isPressed ? value : null)}
                />
              ))}
            </div>

            {/*
              A NATIVE <select> INSIDE ASTRYX'S Field, and it is measured
              rather than lazy: Astryx's Selector is a combobox that exposes
              role=listbox with its own popup, not a <select>. Swapping to it
              would change this control's role and break
              `userEvent.selectOptions`, which is how the closure's own cases
              choose a reason. Field supplies the label wiring and the spacing;
              the element stays the one the platform already gets right.
            */}
            <Field label="Reason" inputID="decision-reason" width="24ch">
              <select
                id="decision-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value as ReasonCode)}
                className="w-full rounded border border-current/25 bg-transparent px-3 py-2 text-[13px]"
              >
                {REASONS.map((code) => (
                  <option key={code} value={code}>
                    {REASON_LABELS[code]}
                  </option>
                ))}
              </select>
            </Field>

            <TextArea
              label="Note"
              rows={3}
              value={note}
              onChange={(next) => setNote(next)}
              width="60ch"
            />

            <Button
              label={busy ? 'Saving' : 'Save decision'}
              variant="primary"
              /* Disabled only while the request is in flight, never for a
                 missing choice: pressing it with no choice is how a reviewer
                 finds out one is needed (#150). */
              isDisabled={busy}
              onClick={() => void save()}
            />
          </div>
        )}

        {problem !== null ? (
          <p role="status">
            <Text as="span">{problem}</Text>
          </p>
        ) : null}
      </Section>
    </div>
  )
}
