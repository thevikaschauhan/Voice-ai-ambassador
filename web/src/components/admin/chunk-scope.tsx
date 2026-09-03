'use client'

import { useCallback, useState } from 'react'
import {
  CONFLICT_EFFECTS,
  CONFLICT_LABELS,
  RETRIEVAL_SCOPES,
  SCOPE_EFFECTS,
  SCOPE_LABELS,
} from '@/lib/admin/knowledge'
import type { KnowledgeChunkView, RetrievalScope } from '@/lib/admin/knowledge'

/**
 * Assigning a chunk's scope, which is a request rather than a decision.
 *
 * `knowledge.py` owns the closure and can overrule what is asked for: a
 * conflict with a structured inventory field keeps the chunk admin-only
 * whatever the reviewer chose, and an unknown project is never publishable. So
 * this control shows the CONSEQUENCE of each option beside it, and where a
 * closure has already fired it says what fired and what that means - rather
 * than showing a chunk that looks assigned and behaves closed.
 *
 * `project_knowledge` cannot be saved without a project id, because binding is
 * what the scope means: prose about a tower we do not sell is prose nobody can
 * check.
 */
export function ChunkScope({
  chunk,
  projectIds,
}: {
  chunk: KnowledgeChunkView
  /** Ids from `data/inventory.json`. The web tier does not invent them. */
  projectIds: readonly string[]
}) {
  const [scope, setScope] = useState<RetrievalScope>(chunk.retrieval_scope)
  const [projectId, setProjectId] = useState<string>(chunk.project_id ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const needsProject = scope === 'project_knowledge'
  const blocked = chunk.conflict_code === 'unknown_project'

  const save = useCallback(async () => {
    setBusy(true)
    setProblem(null)
    try {
      const response = await fetch(`/api/admin/knowledge/chunks/${chunk.id}/reviews`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          action: scope,
          project_id: needsProject ? projectId : null,
        }),
      })
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { error?: string }
        setProblem(payload.error ?? 'That scope was not saved.')
        return
      }
      setSaved(true)
    } catch {
      setProblem('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }, [chunk.id, needsProject, projectId, scope])

  return (
    <div className="flex flex-col gap-3">
      {chunk.conflict_code !== null ? (
        <div className="border border-warn-500/40 px-4 py-3">
          <p className="text-[12px] tracking-[0.1em] text-ink-200 uppercase">
            {CONFLICT_LABELS[chunk.conflict_code]}
          </p>
          <p className="mt-1 max-w-[80ch] text-[12px] leading-relaxed text-ink-400">
            {CONFLICT_EFFECTS[chunk.conflict_code]}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <label
            className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
            htmlFor={`scope-${chunk.id}`}
          >
            Scope
          </label>
          <select
            id={`scope-${chunk.id}`}
            value={scope}
            onChange={(event) => setScope(event.target.value as RetrievalScope)}
            className="border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
          >
            {RETRIEVAL_SCOPES.map((option) => (
              <option key={option} value={option}>
                {SCOPE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        {needsProject ? (
          <div className="flex flex-col gap-1.5">
            <label
              className="text-[11px] tracking-[0.12em] text-ink-400 uppercase"
              htmlFor={`project-${chunk.id}`}
            >
              Project
            </label>
            <select
              id={`project-${chunk.id}`}
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              className="border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
            >
              <option value="">Choose a project</option>
              {projectIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <button
          type="button"
          disabled={busy || blocked || (needsProject && projectId === '')}
          onClick={() => void save()}
          className="border border-ink-600 px-5 py-2.5 text-[13px] text-ink-100 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
        >
          {busy ? 'Saving' : 'Save scope'}
        </button>
      </div>

      {/* What the chosen scope does, next to the choice rather than in a doc. */}
      <p className="max-w-[80ch] text-[12px] leading-relaxed text-ink-500">
        {SCOPE_EFFECTS[scope]}
      </p>

      {saved ? (
        <p className="text-[12px] text-ink-400" role="status">
          Scope saved. Reload to see how the closure resolved it.
        </p>
      ) : null}
      {problem !== null ? (
        <p className="border border-warn-500/40 px-4 py-3 text-[12px] text-ink-300" role="status">
          {problem}
        </p>
      ) : null}
    </div>
  )
}
