'use client'

import { useCallback, useState } from 'react'
import { scopeCanReachACall } from '@/lib/admin/knowledge'
import type { KnowledgeFigureView, RetrievalScope } from '@/lib/admin/knowledge'

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
      <p className="text-[12px] text-ink-500">
        No figures were extracted from this section.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <ol className="flex flex-col gap-3">
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
            <li key={figure.id} className="flex flex-col gap-1.5 border-b border-ink-900 pb-3">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <span className="text-[14px] text-ink-100">{figure.surface}</span>
                {figure.unit === null ? null : (
                  <span className="text-[12px] text-ink-400">{figure.unit}</span>
                )}
                <span className="text-[11px] tracking-[0.1em] text-ink-600 uppercase">
                  {figure.kind}
                </span>
                {figure.page === null ? null : (
                  <span className="text-[11px] text-ink-600">page {figure.page}</span>
                )}
                {governed ? (
                  // Approved or not, this cannot reach a call. Saying "speakable"
                  // here would be the lie the closure exists to prevent.
                  <span className="border border-ink-700 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-ink-400 uppercase">
                    inventory governs this value
                  </span>
                ) : unscoped ? (
                  <span className="border border-ink-700 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-ink-400 uppercase">
                    {approved ? 'approved, but this section is admin-only' : 'not approved'}
                  </span>
                ) : approved ? (
                  <span className="border border-brass-500/50 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-brass-400 uppercase">
                    speakable
                  </span>
                ) : (
                  <span className="border border-ink-700 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-ink-500 uppercase">
                    not approved
                  </span>
                )}
              </div>

              {/* The sentence is the review. A value without it is a number
                  somebody is guessing about. */}
              <p className="max-w-[80ch] text-[12px] leading-relaxed text-ink-300">
                {figure.source_sentence}
              </p>

              <div className="flex gap-3">
                {approved ? (
                  <button
                    type="button"
                    disabled={pending === figure.id}
                    onClick={() => void review(figure.id, 'revoked')}
                    className="border border-ink-600 px-4 py-1.5 text-[12px] text-ink-200 hover:border-warn-500 disabled:opacity-40"
                  >
                    Revoke
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={pending === figure.id}
                    onClick={() => void review(figure.id, 'approved')}
                    className="border border-ink-600 px-4 py-1.5 text-[12px] text-ink-200 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
                  >
                    Approve
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ol>

      {problem !== null ? (
        <p className="border border-warn-500/40 px-5 py-3 text-[12px] text-ink-300" role="status">
          {problem}
        </p>
      ) : null}
    </div>
  )
}
