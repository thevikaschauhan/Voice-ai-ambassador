'use client'

import { useCallback, useState } from 'react'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import { Text } from '@astryxdesign/core/Text'
import { scopeCanReachACall } from '@/lib/admin/knowledge'
import type { KnowledgeFigureView, RetrievalScope } from '@/lib/admin/knowledge'

/**
 * How a control names the occurrence it acts on.
 *
 * The page, when there is one, because that is what separates two occurrences
 * of the same value. When there is not, the first words of the sentence: a
 * figure with no recorded page still needs a name that is not just "Approve",
 * or the ambiguity this exists to remove comes straight back.
 */
function occurrenceOf(figure: KnowledgeFigureView): string {
  if (figure.page !== null) return `page ${figure.page}`
  // Trimmed at a WORD boundary. The first cut of this said
  // `slice(0, 40)` and the browser run read it back as
  // "...20 percent on bookin" - a name that ends mid-word is a name a screen
  // reader mispronounces, and pasted text has no pages, so this branch is the
  // one every pasted document uses.
  const words = figure.source_sentence.split(/\s+/)
  let text = ''
  for (const word of words) {
    if (text.length + word.length + 1 > 40) break
    text = text === '' ? word : `${text} ${word}`
  }
  return `occurrence starting "${text === '' ? figure.source_sentence.slice(0, 40) : text}"`
}

/**
 * The extracted figure list, reviewed one occurrence at a time.
 *
 * Every control here is deliberately singular. `docs/10-` step 6 makes each
 * checked occurrence its own append-only approval record, and there is NO bulk
 * action on purpose: "approve all" is how a reviewer approves a sentence they
 * never read, which is the failure this whole review exists to prevent.
 *
 * An occurrence is not a value. The same figure written twice is two rows, and
 * approving one says nothing about the other - which is why the buttons carry
 * a figure id and never a value.
 *
 * Approval is also not the only condition for a figure being speakable. In an
 * `inventory_governed` chunk the tick is real and the consequence is not:
 * approving a figure never turns inventory-governed material into prompt
 * material, so this says who governs the value instead of calling it speakable.
 */
export function FigureReview({
  documentId,
  figures,
  chunkScope,
}: {
  documentId: string
  figures: readonly KnowledgeFigureView[]
  chunkScope: RetrievalScope
}) {
  const [pending, setPending] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [decided, setDecided] = useState<Record<string, 'approved' | 'revoked'>>({})

  const review = useCallback(
    async (figureId: string, action: 'approved' | 'revoked') => {
      setPending(figureId)
      setProblem(null)
      try {
        const response = await fetch(`/api/admin/knowledge/figures/${figureId}/reviews`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ action, document_id: documentId }),
        })
        if (!response.ok) {
          const payload = (await response.json().catch(() => ({}))) as { error?: string }
          setProblem(payload.error ?? 'That review was not saved.')
          return
        }
        setDecided((current) => ({ ...current, [figureId]: action }))
      } catch {
        setProblem('Could not reach the server.')
      } finally {
        setPending(null)
      }
    },
    [documentId],
  )

  if (figures.length === 0) {
    return (
      <EmptyState isCompact title="No figures were extracted from this section." />
    )
  }

  const approvedCount = figures.filter(
    (figure) =>
      decided[figure.id] === 'approved' ||
      (decided[figure.id] === undefined && figure.active_approval_id !== null),
  ).length

  return (
    <div className="flex flex-col gap-3">
      {/*
        THE SUMMARY FIRST (finding G7: "no per-chunk summary"). "4 figures, 1
        approved" is the whole question a reviewer has about a section they
        have not opened yet; without it they had to count pills down the page.
        Counted from the SAME optimistic state the rows render from, so it
        moves the instant a figure is approved rather than disagreeing with
        the rows above it until a reload.
      */}
      <Text as="p" display="block" type="supporting" color="secondary">
        {`${figures.length === 1 ? '1 figure' : `${figures.length} figures`}, ${approvedCount} approved`}
      </Text>

      <ol className="flex flex-col">
        {figures.map((figure) => {
          const state = decided[figure.id]
          const approved =
            state === 'approved' || (state === undefined && figure.active_approval_id !== null)
          // Three reasons a figure is not speakable, and they are different
          // things to tell a reviewer. `inventory_governed` is closed
          // PERMANENTLY - the value comes from data/inventory.json and no
          // approval changes that. `admin_only` (the DEFAULT) is closed until
          // somebody scopes the chunk, which is an action the reviewer can
          // take. Saying "inventory governs this" about an unscoped chunk
          // names the wrong cause and points at the wrong fix.
          const governed = chunkScope === 'inventory_governed'
          const unscoped = !governed && !scopeCanReachACall(chunkScope)

          return (
            /*
              ONE DENSE ROW, not a Card each (finding G7: "four figures fill a
              screen"). A hairline between rows instead of four padded cards,
              and the value, its kind, the sentence it came from and the
              control that acts on it all on one line above lg. A document
              with a dozen extracted numbers is a normal payment-plan PDF, and
              it used to be a dozen screens of scrolling.
            */
            <li
              key={figure.id}
              data-testid="figure-row"
              className="flex flex-col gap-2 border-b border-[var(--color-border)] py-3 last:border-b-0 lg:flex-row lg:items-baseline lg:gap-4"
            >
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 lg:w-[38%] lg:shrink-0">
                <Text as="span" type="large" weight="semibold">
                  {figure.surface}
                </Text>
                {figure.unit === null ? null : (
                  <Text as="span" color="secondary">
                    {figure.unit}
                  </Text>
                )}
                <Text as="span" type="supporting" color="secondary">
                  {figure.kind}
                </Text>
                {figure.page === null ? null : (
                  <Text as="span" type="supporting" color="secondary">
                    page {figure.page}
                  </Text>
                )}
                {/*
                  ONE OF FOUR STATES, and only one of them means the value can
                  reach a buyer - so that one is the only `success`. The other
                  three were four near-identical bordered spans separated by
                  ink-grey against brass, which is a distinction by colour on
                  the one screen where getting it wrong puts an unreviewed
                  number in an ambassador's mouth.
                */}
                {/*
                  THE WEIGHTS FOLLOW PR E'S RULE, not the card's literal
                  "approved in brass": brass marks WHAT NEEDS A REVIEWER,
                  which is why `qualified` is neutral on the leads list while
                  `unreviewed` is brass. An approved figure painted brass
                  would make brass mean "done" here and "needs you" there -
                  two vocabularies again, the thing pair 1 just fixed in the
                  lead detail. So the ACTION is brass and finished work is
                  quiet.

                  No `warning` anywhere: an unscoped section is the DEFAULT
                  state of a freshly parsed document, so yellow told a
                  reviewer something had gone wrong on every figure of every
                  new document. The WORDS still separate the four states,
                  which is what a colour-blind reviewer reads.
                */}
                {governed ? (
                  // Approved or not, this cannot reach a call. Saying "speakable"
                  // here would be the lie the closure exists to prevent.
                  <Badge variant="neutral" label="inventory governs this value" />
                ) : unscoped ? (
                  <Badge
                    variant={approved ? 'neutral' : 'accent'}
                    label={approved ? 'approved, but this section is admin-only' : 'not approved'}
                  />
                ) : approved ? (
                  <Badge variant="neutral" label="speakable" />
                ) : (
                  <Badge variant="accent" label="not approved" />
                )}
              </div>

              {/* The sentence is the review. A value without it is a number
                  somebody is guessing about. `min-w-0` so a long sentence
                  wraps inside its share of the row instead of pushing the
                  action off the end. */}
              <Text as="p" display="block">
                {figure.source_sentence}
              </Text>

              <div className="flex gap-3 lg:ml-auto lg:shrink-0">
                {/*
                  THE VISIBLE WORD IS STILL "Approve"; the ACCESSIBLE NAME says
                  which occurrence. Two occurrences of one value produce two
                  buttons, and while both were named "Approve" a screen reader
                  user had nothing to choose between them - on the one screen
                  where the wrong press makes a figure speakable in a context
                  nobody reviewed. Named by the page, because that is the fact
                  that separates them; `occurrence` is the fallback for a
                  figure whose page was never recorded, since "Approve" alone
                  would put the ambiguity straight back.
                */}
                {approved ? (
                  <Button
                    label="Revoke"
                    aria-label={`Revoke ${figure.surface}, ${occurrenceOf(figure)}`}
                    variant="secondary"
                    isDisabled={pending === figure.id}
                    onClick={() => void review(figure.id, 'revoked')}
                  />
                ) : (
                  <Button
                    label="Approve"
                    aria-label={`Approve ${figure.surface}, ${occurrenceOf(figure)}`}
                    variant="secondary"
                    isDisabled={pending === figure.id}
                    onClick={() => void review(figure.id, 'approved')}
                  />
                )}
              </div>
            </li>
          )
        })}
      </ol>

      {problem !== null ? (
        <p role="status">
          <Text as="span">{problem}</Text>
        </p>
      ) : null}
    </div>
  )
}
