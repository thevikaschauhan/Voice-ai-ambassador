'use client'

import { useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button } from '@astryxdesign/core/Button'
import { Text } from '@astryxdesign/core/Text'
import { Field } from '@astryxdesign/core/Field'
import { Badge } from '@astryxdesign/core/Badge'
import type { DocumentDetail, KnowledgeChunkView, KnowledgePublicationRequest, RetrievalScope } from '@/lib/admin/knowledge'
import { CONFLICT_LABELS } from '@/lib/admin/knowledge'
import { FigureReview } from './figure-review'

type Context = 'general_knowledge' | 'project_knowledge'
type Choice = { action: RetrievalScope | 'inherit'; project: string }
const control = 'w-full rounded-[var(--radius-element)] border border-[var(--color-border-emphasized)] bg-[var(--color-background-surface)] px-3 py-2 text-sm'

/** A heading owns ALL its consecutive retrieval chunks, including overlaps. */
function sectionsOf(chunks: KnowledgeChunkView[]) {
  const sections: { id: string; title: string; chunks: KnowledgeChunkView[] }[] = []
  for (const chunk of chunks) {
    const previous = sections.at(-1)
    if (previous && previous.chunks[0].heading === chunk.heading) previous.chunks.push(chunk)
    else sections.push({id: chunk.id, title: chunk.heading || 'Document content', chunks: [chunk]})
  }
  return sections
}

/** Remove only exact paragraph overlap at an adjacent chunk boundary. */
function readable(chunks: KnowledgeChunkView[], source: boolean) {
  const paragraphs: string[] = []
  for (const chunk of chunks) {
    const next = (source ? chunk.body : chunk.review_body ?? '').split('\n\n')
    let overlap = Math.min(paragraphs.length, next.length)
    while (overlap > 0 && paragraphs.slice(-overlap).join('\n\n') !== next.slice(0, overlap).join('\n\n')) overlap--
    paragraphs.push(...next.slice(overlap))
  }
  return paragraphs.join('\n\n')
}

export function DocumentReview({ document, projects }: {
  document: DocumentDetail
  projects: { id: string; name: string }[]
}) {
  const router = useRouter()
  const sections = useMemo(() => sectionsOf(document.chunks), [document.chunks])
  const first = document.chunks.find(c => c.retrieval_scope === 'project_knowledge')
  const [context, setContext] = useState<Context>(first ? 'project_knowledge' : 'general_knowledge')
  const [project, setProject] = useState(first?.project_id ?? '')
  const [choices, setChoices] = useState<Record<string, Choice>>(() => Object.fromEntries(sections.map(s => {
    const blocked = s.chunks.some(c => c.conflict_code || c.retrieval_scope === 'inventory_governed')
    const uniform = s.chunks.every(c => c.retrieval_scope === s.chunks[0].retrieval_scope && c.project_id === s.chunks[0].project_id)
    const saved = s.chunks[0]
    const action = blocked || !uniform ? 'admin_only' : saved.retrieval_scope === 'admin_only'
      ? (document.status === 'published' || document.status === 'archived' ? 'admin_only' : 'inherit') : saved.retrieval_scope
    return [s.id, { action, project: saved.project_id ?? '' }]
  })))
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [published, setPublished] = useState(false)
  const retry = useRef<KnowledgePublicationRequest | null>(null)
  const canPublish = document.status === 'draft' || document.status === 'published'
  const included = sections.filter(s => ['inherit', 'general_knowledge', 'project_knowledge'].includes(choices[s.id].action))
  const withheld = document.chunks.flatMap(c => c.figures).filter(f => f.active_approval_id === null).length
  const reset = () => { setConfirmed(false); setMessage(null); setPublished(false); retry.current = null }
  function change(id: string, value: Choice) { reset(); setChoices(current => ({...current, [id]: value})) }

  async function publish() {
    if (busy || stale) return
    if ((context === 'project_knowledge' && !project && included.some(s => choices[s.id].action === 'inherit')) ||
        included.some(s => choices[s.id].action === 'project_knowledge' && !choices[s.id].project)) {
      setMessage('Choose a project for the included project content.'); return
    }
    if (!confirmed) { setMessage('Confirm that you have reviewed the included content.'); return }
    const payload = retry.current ?? {
      request_id: crypto.randomUUID(), expected_revision: document.revision,
      expected_review_token: document.review_token!, confirmed: true,
      selections: sections.flatMap(s => s.chunks.map(c => {
        const choice = choices[s.id]
        const action = choice.action === 'inherit' ? context : choice.action
        return {chunk_id: c.id, action, project_id: action === 'project_knowledge' ? (choice.action === 'inherit' ? project : choice.project) : null}
      })),
    }
    retry.current = payload
    setBusy(true); setMessage(null)
    try {
      const response = await fetch(`/api/admin/knowledge/documents/${document.id}/publish`, {
        method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(payload),
      })
      if (response.status === 409) {
        setStale(true); setMessage('Another review changed this document. Reload the latest review before publishing. Your selections are shown here for reference.'); return
      }
      if (response.status === 401) { setMessage('Your session has expired. Sign in again before publishing.'); return }
      if (!response.ok) {
        setMessage(response.status === 422 ? 'Publication was refused. Check the content and project selections, then reload the latest review.' : 'Publication could not be confirmed. Retry to check the same request safely.'); return
      }
      setPublished(true)
      setMessage(`Published. ${included.length} ${included.length === 1 ? 'section is' : 'sections are'} available to the ambassador; excluded content and unapproved document numbers remain withheld.`)
      router.refresh()
    } catch { setMessage('Publication could not be confirmed. Retry to check the same request safely.') }
    finally { setBusy(false) }
  }

  return <div className="flex min-w-0 flex-col gap-8">
    <div className="flex flex-col gap-2">
      <Text as="h2" type="large" weight="semibold">Review this document</Text>
      <Text as="p" display="block" color="secondary">Read the included content, choose its context, and publish once. Prices, payment plans, sizes, handover and other inventory facts must stay excluded.</Text>
      {document.status === 'published' && <Text as="p" display="block" color="secondary">The published content remains available while you review. Section changes take effect together when you publish.</Text>}
      {!document.review_token && <p role="status">Document publishing requires the updated admin API. Reload after it is available.</p>}
      {!canPublish && <p>This revision cannot be published. Add a new document with successfully extracted text.</p>}
    </div>
    <fieldset disabled={busy || stale || published || !canPublish} className="flex min-w-0 flex-col gap-8 disabled:opacity-70">
      <legend className="sr-only">Document review choices</legend>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Document context" inputID="document-context">
          <select id="document-context" className={control} value={context} onChange={e => { reset(); setContext(e.target.value as Context) }}>
            <option value="general_knowledge">General guidance</option><option value="project_knowledge">Project description</option>
          </select>
        </Field>
        {context === 'project_knowledge' && <Field label="Document project" inputID="document-project">
          <select id="document-project" className={control} value={project} onChange={e => { reset(); setProject(e.target.value) }}>
            <option value="">Select a project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>}
      </div>
      <div className="flex flex-wrap gap-3" aria-label="Review summary">
        <Badge variant="neutral" label={`${included.length} included`} />
        <Badge variant="neutral" label={`${sections.length - included.length} excluded`} />
        <Badge variant="neutral" label={`${withheld} number occurrences withheld`} />
      </div>
      <div className="flex min-w-0 flex-col gap-8">
        {sections.map(section => {
          const choice = choices[section.id]
          const excluded = ['admin_only', 'inventory_governed'].includes(choice.action)
          const blocked = section.chunks.some(c => c.conflict_code || c.retrieval_scope === 'inventory_governed')
          return <section key={section.id} className="min-w-0 border-t border-[var(--color-border)] pt-5" aria-labelledby={`heading-${section.id}`}>
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex flex-col gap-1">
                <h3 id={`heading-${section.id}`} className="text-lg font-semibold break-words">{section.title}</h3>
                <Text as="p" display="block" type="supporting" color="secondary">{excluded ? 'Excluded from the ambassador' : 'Included in this publication'}</Text>
              </div>
              <div className="w-full sm:max-w-64">
                <label className="sr-only" htmlFor={`use-${section.id}`}>Use {section.title}</label>
                <select id={`use-${section.id}`} className={control} value={choice.action} disabled={blocked} onChange={e => change(section.id, {...choice, action: e.target.value as Choice['action']})}>
                  <option value="inherit">Use document context</option><option value="general_knowledge">General guidance</option><option value="project_knowledge">Different project context</option><option value="admin_only">Exclude section</option><option value="inventory_governed">Managed in inventory</option>
                </select>
              </div>
            </div>
            {blocked && <p className="mb-4 text-sm text-[var(--color-text-secondary)]">{section.chunks.some(c => c.retrieval_scope === 'inventory_governed') ? 'Managed in inventory. This section remains excluded.' : CONFLICT_LABELS[section.chunks.find(c => c.conflict_code)!.conflict_code!]} Review the source or inventory before adding a corrected document.</p>}
            {choice.action === 'project_knowledge' && <div className="mb-4 max-w-sm"><Field label={`Project for ${section.title}`} inputID={`project-${section.id}`}><select id={`project-${section.id}`} value={choice.project} className={control} onChange={e => change(section.id, {...choice, project: e.target.value})}><option value="">Select a project</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field></div>}
            {!excluded && <div className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-7" data-testid="publication-preview">{readable(section.chunks, false) || 'Preview unavailable. Reload the document before publishing.'}</div>}
            <details className="mt-4">
              <summary className="cursor-pointer py-2 text-sm underline underline-offset-4">{excluded ? 'Read excluded source' : 'Compare with source text'}</summary>
              <p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-7">{readable(section.chunks, true)}</p>
              {section.chunks.some(c => c.page_start !== null) && <p className="mt-2 text-sm">Source pages: {section.chunks.map(c => c.page_start).filter(p => p !== null).join(', ')}</p>}
            </details>
          </section>
        })}
      </div>
      <label className="flex items-start gap-3 text-sm leading-6">
        <input type="checkbox" className="mt-1 size-4 shrink-0 accent-[var(--color-accent)]" checked={confirmed} onChange={e => {setConfirmed(e.target.checked); retry.current = null}} />
        <span>I have reviewed the included content and its project context, and excluded inventory facts and unsupported claims. Automated checks do not verify factual accuracy.</span>
      </label>
    </fieldset>
    <div className="sticky bottom-0 flex flex-wrap items-center gap-4 border-t border-[var(--color-border)] bg-[var(--color-background-body)] py-4">
      <Button label={busy ? 'Publishing' : 'Publish reviewed content'} variant="primary" isDisabled={busy || stale || published || !canPublish || !document.review_token || included.length === 0} onClick={() => void publish()} />
      {included.length === 0 && <p className="text-sm">No content is selected for publication. Include a reviewed section to continue.</p>}
      {stale && <Button label="Reload latest review" variant="secondary" onClick={() => window.location.reload()} />}
      {message && <p role="status" className="max-w-[65ch] text-sm">{message}</p>}
    </div>
    <details className="border-t border-[var(--color-border)] pt-4">
      <summary className="cursor-pointer py-2 font-semibold">Optional number approvals</summary>
      <p className="my-3 max-w-[75ch] text-sm text-[var(--color-text-secondary)]">You can publish without approving these numbers. Each approval or revocation is saved immediately and may affect published content. Reload the document afterwards to refresh the preview.</p>
      {document.chunks.map(c => c.figures.length > 0 && <div key={c.id} className="my-5 min-w-0"><Text as="h3" weight="semibold">{c.heading || 'Document content'}</Text><FigureReview documentId={document.id} figures={c.figures} chunkScope={c.retrieval_scope} onReviewed={() => {setStale(true); setConfirmed(false); setMessage('A number approval changed. Reload the latest review to see the updated preview before publishing.')}} /></div>)}
      {document.chunks.every(c => c.figures.length === 0) && <p className="text-sm">No numbers were extracted.</p>}
    </details>
  </div>
}
