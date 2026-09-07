'use client'

import { useCallback, useState } from 'react'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Field } from '@astryxdesign/core/Field'
import { Text } from '@astryxdesign/core/Text'
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
    // Answered HERE rather than by disabling the control. `blocked` is the one
    // state that still disables it, and the difference is the whole rule: a
    // missing project is an input the reviewer can supply, so the press asks
    // for it; an `unknown_project` closure is the SERVER refusing whatever
    // they pick, so there is nothing to ask for.
    if (needsProject && projectId === '') {
      setSaved(false)
      setProblem('Choose a project for this scope.')
      return
    }

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
        /*
          A Card with a warning badge rather than Astryx's Banner: Banner
          renders role=alert for its warning status, and this conflict is
          state the page was loaded with, not something that just happened.
          An alert region announcing on load talks over whatever the reviewer
          was reading.
        */
        <Card padding={4}>
          <div className="flex flex-col gap-2">
            <Badge variant="warning" label={CONFLICT_LABELS[chunk.conflict_code]} />
            <Text as="p" display="block" color="secondary">
              {CONFLICT_EFFECTS[chunk.conflict_code]}
            </Text>
          </div>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-end gap-4">
        {/*
          NATIVE <select>s INSIDE ASTRYX'S Field, and measured rather than
          lazy: Astryx's Selector is a combobox exposing role=listbox through
          its own popup, not a <select>. Adopting it would change these
          controls' role and break `userEvent.selectOptions`, which is how the
          closure's cases choose a scope and a project. Field gives the label
          wiring and the spacing; the element stays the one the platform gets
          right. Astryx 0.5.3 ships no native-select component.
        */}
        <Field label="Scope" inputID={`scope-${chunk.id}`}>
          <select
            id={`scope-${chunk.id}`}
            value={scope}
            onChange={(event) => setScope(event.target.value as RetrievalScope)}
            /*
              STYLED FROM THE THEME, not from the current colour (finding
              G7: "native unstyled <select> beside an Astryx button"). It was
              always inside Astryx's Field - the label wiring was never the
              problem - but the old border came from border-current/25, which
              takes the TEXT colour at an opacity, so it sat beside themed
              controls looking like neither. The token below is the one every
              bordered surface uses, and --radius-element is the same 4px the
              buttons carry.
            */
            className="w-full rounded-[var(--radius-element)] border border-[var(--color-border)] bg-[var(--color-background-surface)] px-3 py-2 text-[13px]"
          >
            {RETRIEVAL_SCOPES.map((option) => (
              <option key={option} value={option}>
                {SCOPE_LABELS[option]}
              </option>
            ))}
          </select>
        </Field>

        {needsProject ? (
          <Field label="Project" inputID={`project-${chunk.id}`}>
            <select
              id={`project-${chunk.id}`}
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              /*
              STYLED FROM THE THEME, not from the current colour (finding
              G7: "native unstyled <select> beside an Astryx button"). It was
              always inside Astryx's Field - the label wiring was never the
              problem - but the old border came from border-current/25, which
              takes the TEXT colour at an opacity, so it sat beside themed
              controls looking like neither. The token below is the one every
              bordered surface uses, and --radius-element is the same 4px the
              buttons carry.
            */
            className="w-full rounded-[var(--radius-element)] border border-[var(--color-border)] bg-[var(--color-background-surface)] px-3 py-2 text-[13px]"
            >
              <option value="">Choose a project</option>
              {projectIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        <Button
          label={busy ? 'Saving' : 'Save scope'}
          variant="primary"
          /* Both halves kept from #150: disabled while the request is in
             flight, and disabled when the SERVER has already refused this
             chunk - which is not a missing input. A missing project is
             answered by pressing it, not by fading it. */
          isDisabled={busy || blocked}
          onClick={() => void save()}
        />
      </div>

      {/* What the chosen scope does, next to the choice rather than in a doc. */}
      <Text as="p" display="block" color="secondary">
        {SCOPE_EFFECTS[scope]}
      </Text>

      {saved ? (
        <p role="status">
          <Text as="span">Scope saved. Reload to see how the closure resolved it.</Text>
        </p>
      ) : null}
      {problem !== null ? (
        <p role="status">
          <Text as="span">{problem}</Text>
        </p>
      ) : null}
    </div>
  )
}
