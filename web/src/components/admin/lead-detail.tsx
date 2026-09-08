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
import { CategoryScore, ScoreTotal } from './score'
import { endReasonLabel, REASON_LABELS } from '@/lib/admin/leads'
import type { LeadDetailRecord, ReasonCode } from '@/lib/admin/leads'

/**
 * One lead, and the decision a human makes about it.
 *
 * docs/10-: the detail makes model provenance visible. The summary is LABELLED
 * generated, because an unlabelled model sentence reads as a fact somebody
 * checked. The score shows every signal, including the ones that scored
 * NOTHING, so the breakdown is the whole rubric rather than its highlights. The
 * decision history is shown and never edited, because it is append-only in the
 * database (ADR-020) and a UI that looked editable would be lying about that.
 *
 * THE SCORE'S EVIDENCE TURNS AND THE TRANSCRIPT ARE NOT SHOWN, by the human's
 * decision on 2026-09-08: "Remove turn mention in lead section it doesn't
 * provide any value. Remove Buyer turns cited by the score". This reverses the
 * "showing score evidence" half of docs/10-'s provenance principle and nothing
 * else - the generated label and the immutable history stay. `lead.turns` is
 * still in the record and still comes down the wire; no part of this page reads
 * it, and `admin-leads.test.tsx` asserts the word "turn" appears nowhere in the
 * rendered detail.
 *
 * Each category is a 0-100 figure in a colour band; see `./score` for why the
 * rows no longer add up to the total and where the rubric's weights went.
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

  /**
   * Whether the analysis produced anything worth its own cards.
   *
   * The three FIELDS, not `analysis_status`: a lead can sit at 'complete'
   * with a null score if the rubric declined to score it, and one at
   * 'pending' can already carry a summary. The question this answers is "is
   * there anything to show", and the fields are what answer it - the same
   * reasoning `toScore` uses in leads.server.ts, where consulting
   * analysis_status instead of the fields was the wrong instinct.
   *
   * TURNS ARE DELIBERATELY NOT PART OF THIS, and including them was a defect
   * the browser caught: a failed analysis on a real call still HAS a
   * transcript, so `turns.length > 0` made this true and rendered both cards
   * again - "Analysis failed" in one, "No score" in the next - which is the
   * pile of empty cards the collapse exists to remove. Since 2026-09-08 the
   * transcript is not rendered at all, so counting it here would make this
   * predicate true on the strength of something nobody can see.
   */
  const analysed = lead.summary !== null || lead.score !== null

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
        THE HEADER BLOCK (finding G5). Everything that identifies this call in
        one place: which call, what language, how it ended, and where the
        review stands. It was a line of supporting text with a badge stranded
        beside it, so the page opened with its least useful information at
        full size and its identity in 12px grey.

        No h1 here. The session id is the page heading and AdminAppShell
        renders it, so this component adding one gave /admin/leads/<id> two
        level-one headings. The id is repeated in this block as SUPPORTING
        text on purpose - a reviewer cross-referencing a log needs it beside
        the facts, not only in the title bar.
      */}
      <div
        data-testid="lead-header"
        className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] pb-4"
      >
        <Text as="span" type="supporting" color="secondary">
          {lead.session_id}
        </Text>
        <Text as="span" type="supporting" color="secondary">
          {endReasonLabel(lead.call_end_reason)}
          {lead.ended_cleanly ? '' : ' - incomplete'}
          {' · '}
          {lead.language.toUpperCase()}
        </Text>
        <LeadStatusBadge status={lead.status} />
      </div>

      {/*
        TWO COLUMNS ABOVE lg (finding G5: "six full-width stacked cards ... the
        decision form is below the fold"). The evidence on the left, the
        decision beside it rather than under it - which is the only change
        that makes the form reachable without scrolling on a 900px viewport.
        One column below lg, because two 300px columns are worse than a
        scroll.

        `items-start` so the two columns do not stretch each other to the
        taller one's height: a short aside next to a long transcript would
        otherwise render a card with a lot of nothing under its content.
      */}
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div data-testid="detail-main" className="flex flex-col gap-6">
          {analysed ? (
            <>
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
                <ScoreTotal total={lead.score.total} />
                {/*
                  Every signal the rubric ran, including the ones that scored
                  nothing: a breakdown that drops its zeros reads as a shorter
                  rubric than the one that produced the total.

                  THE RULE ABOVE THE LIST IS NOT DECORATION. Found in the
                  browser: the total's own meter sat 36px under the figure and
                  28px over the first category's label, so the eye paired it
                  with "Budget stated" instead. Eight bars in a column need one
                  break to say which of them is the total.
                */}
                <ol className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-4">
                  {lead.score.breakdown.map((item) => (
                    <li key={item.signal}>
                      <CategoryScore item={item} />
                    </li>
                  ))}
                </ol>
              </>
            )}
          </Section>

            </>
          ) : (
            /*
              FOUR CARDS SAYING NOTHING BECOME ONE. An unanalysed lead used to
              render "No summary yet.", "No score: the analysis has not
              completed." and an empty turns card - three full-width cards,
              three scroll-lengths, one fact. The reviewer's next step is to
              wait or to retry, and neither needs three cards to say.

              Conditional, not a deletion: the sections come back the moment
              there is something in them, which the inverse case asserts.
            */
            <Section heading="Awaiting analysis">
              <Text as="p" display="block" color="secondary">
                {lead.analysis_status === 'failed'
                  ? 'The analysis failed, so there is no summary or score for this call. The call itself was still recorded.'
                  : 'The analysis has not completed, so there is no summary or score yet. It runs after the call ends.'}
              </Text>
            </Section>
          )}

        </div>

        <div data-testid="detail-aside" className="flex flex-col gap-6">
          <Section heading="Contact">
            <Text as="p" display="block">
              {lead.contact.status === 'captured'
                ? [lead.contact.name, lead.contact.phone, lead.contact.email]
                    .filter((value) => value !== null && value !== '')
                    .join(' · ')
                : `Not captured (${lead.contact.status.replace('_', ' ')})`}
            </Text>
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
                {/*
                  ONE SEGMENTED CONTROL, and NOT Astryx's SegmentedControl.
                  Measured rather than preferred: that component is a
                  RADIOGROUP - role=radio, aria-checked, arrow-key
                  selection-follows-focus - so adopting it would replace
                  `role=button` + `aria-pressed` with radio semantics. The
                  card asks for a segmented control "(aria-pressed kept)", and
                  both cannot be true of Astryx's component; the accessibility
                  contract the closure's cases pin is worth more than the
                  component. It also could not express clearing the choice,
                  which the toggles below do.

                  So the PATTERN goes on the ToggleButtons: one bordered
                  container, no gap, a hairline between the halves. Each half
                  is still a button that says whether it is pressed.
                */}
                <div
                  data-segmented
                  className="inline-flex w-fit overflow-hidden rounded-[var(--radius-element)] border border-[var(--color-border)] [&>*+*]:border-l [&>*+*]:border-[var(--color-border)]"
                >
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
      </div>
    </div>
  )
}
