'use client'

import { Empty, Field, Panel } from '@/components/panel'
import { aed, isUnverified, num, quarter, sqft } from '@/lib/format'
import { milestoneAmounts, resolveShortlist } from '@/lib/inventory.shared'
import type { SessionState } from '@/lib/session/state'
import { STAGES, type Project, type Stage } from '@/lib/types'

interface AmbassadorViewProps {
  state: SessionState
  projects: readonly Project[]
}

export function AmbassadorView({ state, projects }: AmbassadorViewProps) {
  const brief = state.brief
  const { resolved, unresolved } = resolveShortlist(brief?.shortlist_ids ?? [], projects)

  return (
    <Panel title="Ambassador view" audience="The commercial stakeholder">
      <div className="space-y-7">
        <StageBar stage={brief?.stage ?? 'opening'} />

        {state.escalation ? <EscalationNotice reason={state.escalation.reason} /> : null}

        {brief === null ? (
          <Empty>
            The brief is extracted after each turn by a separate small-model call and
            validated before it appears. Nothing is shown until it has parsed.
          </Empty>
        ) : (
          <section>
            <h3 className="mb-2 text-[12px] tracking-[0.12em] text-ink-500 uppercase">
              Lead brief
            </h3>
            <dl>
              <Field label="Intent">{brief.intent}</Field>
              <Field label="Budget">
                <Budget state={state} />
              </Field>
              {brief.unit_preference ? (
                <Field label="Unit preference">{brief.unit_preference}</Field>
              ) : null}
              {brief.timeline ? <Field label="Timeline">{brief.timeline}</Field> : null}
              {brief.buyer_location ? (
                <Field label="Buyer location">{brief.buyer_location}</Field>
              ) : null}
              {brief.golden_visa_interest !== null ? (
                <Field label="Golden visa">
                  {brief.golden_visa_interest ? 'interested' : 'not raised'}
                </Field>
              ) : null}
              {brief.hesitations.length > 0 ? (
                <Field label="Hesitations">{brief.hesitations.join('; ')}</Field>
              ) : null}
            </dl>
          </section>
        )}

        <section>
          <h3 className="mb-2 text-[12px] tracking-[0.12em] text-ink-500 uppercase">
            Shortlist
          </h3>
          {resolved.length === 0 && unresolved.length === 0 ? (
            <Empty>No project has been recommended yet.</Empty>
          ) : (
            <ul className="space-y-5">
              {resolved.map((project) => (
                <ShortlistItem key={project.id} project={project} />
              ))}
              {unresolved.map((id) => (
                <li key={id} className="border-l border-flag-500 pl-4">
                  <p className="text-[13px] text-flag-400">
                    {id} is not in data/inventory.json
                  </p>
                  <p className="mt-1 text-[12px] leading-relaxed text-ink-500">
                    The agent checks shortlist ids against inventory, so an unresolved id
                    is a hole in that check. It is shown rather than dropped.
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        {state.booking ? (
          <section className="border-l border-brass-600 pl-4">
            <p className="text-[12px] tracking-wide text-ink-500">Booking read back</p>
            <p className="mt-1 text-[13px] text-ink-200">{state.booking.slot}</p>
            <p className="mt-1 text-[12px] text-ink-500">
              Read back only. No calendar is written in this build.
            </p>
          </section>
        ) : null}
      </div>
    </Panel>
  )
}

function Budget({ state }: { state: SessionState }) {
  const budget = state.brief?.budget
  const settled = state.budgetSettled
  if (!budget && !settled) return <span className="text-ink-500">not stated</span>

  // docs/04-: the deterministic policy and the model-inferred flag are
  // different sources, named apart on purpose, and the policy wins.
  const currency = settled?.currency ?? budget?.currency ?? ''
  return (
    <span className="space-y-1">
      <span className="block">
        {budget ? `${currency} ${num(budget.amount)}` : currency}
      </span>
      <span className="block text-[11px] text-ink-500">
        {settled
          ? 'currency settled by the confirmation policy'
          : budget?.confirmed
            ? 'model-inferred, not settled by the policy'
            : 'unconfirmed'}
      </span>
    </span>
  )
}

function StageBar({ stage }: { stage: Stage }) {
  const at = STAGES.indexOf(stage)
  return (
    <nav aria-label="Conversation stage">
      <ol className="flex flex-wrap gap-x-1 gap-y-2">
        {STAGES.map((s, i) => {
          const current = i === at
          const past = i < at
          const escalated = s === 'escalated' && current
          return (
            <li key={s} className="flex-1 basis-[74px]">
              <span
                aria-current={current ? 'step' : undefined}
                className={`block border-t-2 pt-2 text-[11px] tracking-wide ${
                  escalated
                    ? 'border-flag-500 text-flag-400'
                    : current
                      ? 'border-brass-500 text-brass-400'
                      : past
                        ? 'border-ink-600 text-ink-400'
                        : 'border-ink-800 text-ink-600'
                }`}
              >
                {s}
              </span>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

function EscalationNotice({ reason }: { reason: string }) {
  return (
    <section
      className="border border-flag-500/40 bg-flag-500/[0.06] px-4 py-3.5"
      aria-label="Escalation"
    >
      <p className="text-[12px] tracking-[0.12em] text-flag-400 uppercase">
        Handed to a human
      </p>
      <p className="mt-1.5 text-[13px] leading-relaxed text-ink-200">
        An ambassador has been notified: {reason}.
      </p>
      <p className="mt-1.5 text-[12px] leading-relaxed text-ink-400">
        This is a designed outcome, not an error. The routing write is stubbed to a
        console log behind an interface, and the notification fires from the same path
        the budget policy uses, so a spoken handoff always has somebody on the other end.
      </p>
    </section>
  )
}

function ShortlistItem({ project }: { project: Project }) {
  const size = sqft(project.size_sqft_min, project.size_sqft_max)
  const milestones = milestoneAmounts(project)
  const unverified = isUnverified(project.source_ref, project.last_verified)

  return (
    <li className="border-l border-ink-700 pl-4">
      <p className="text-[14px] text-ink-100">{project.name}</p>
      <p className="mt-0.5 text-[12px] text-ink-500">{project.area}</p>

      <dl className="mt-2.5">
        {project.status === 'branded_enquiry' ? (
          <Field label="Price">
            <span className="text-ink-400">on enquiry, never quoted</span>
          </Field>
        ) : project.price_from_aed !== null ? (
          <Field label="From">{aed(project.price_from_aed)}</Field>
        ) : null}
        {size ? <Field label="Size">{size}</Field> : null}
        {project.handover ? (
          <Field label="Handover">
            {quarter(project.handover.quarter, project.handover.year)}
          </Field>
        ) : null}
        {milestones.map((m) => (
          <Field key={m.label} label={`${m.label} (${m.pct}%)`}>
            {aed(m.aed)}
          </Field>
        ))}
      </dl>

      {unverified ? (
        <p className="mt-2 text-[11px] leading-relaxed text-warn-500">
          Illustrative figures. This record still carries a VERIFY: marker pending the
          Binghatti price sheet.
        </p>
      ) : null}
    </li>
  )
}
