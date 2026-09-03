import Link from 'next/link'
import { ChunkScope } from '@/components/admin/chunk-scope'
import { FigureReview } from '@/components/admin/figure-review'
import { headers } from 'next/headers'
import { loadInventory } from '@/lib/inventory'
import { readDocument } from '@/lib/admin/knowledge.server'
import { SCOPE_LABELS } from '@/lib/admin/knowledge'

export const dynamic = 'force-dynamic'

/**
 * One document, chunk by chunk: the source text, its scope, and its figures.
 *
 * The project ids offered for binding come from `data/inventory.json` through
 * the reader this surface already uses, not from a list typed here - the whole
 * point of `project_knowledge` is that the project exists in inventory.
 */
export default async function KnowledgeDocumentPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  const [read, projects] = await Promise.all([
    readDocument(
      new Request(`https://admin.local/admin/knowledge/${id}`, {
        headers: { cookie: (await headers()).get('cookie') ?? '' },
      }),
      id,
    ),
    loadInventory().catch(() => []),
  ])
  const projectIds = projects.map((project) => project.id)

  return (
    <main className="mx-auto flex min-h-screen max-w-[1000px] flex-col gap-6 px-4 py-6 sm:px-6">
      <Link className="text-[12px] text-ink-400 hover:text-brass-400" href="/admin/knowledge">
        All documents
      </Link>

      {read.state === 'unauthenticated' ? (
        <p className="border border-ink-700 px-5 py-3.5 text-[13px] text-ink-300">
          <Link className="underline hover:text-brass-400" href="/admin">
            Sign in
          </Link>{' '}
          to review this document.
        </p>
      ) : read.state === 'unavailable' ? (
        <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300">
          {read.reason}
        </p>
      ) : (
        <>
          <header>
            <h1 className="text-[15px] tracking-[0.1em] text-ink-100">{read.data.title}</h1>
            <p className="mt-1.5 text-[12px] text-ink-500">
              {read.data.source_type.toUpperCase()} · revision {read.data.revision} ·{' '}
              {read.data.status}
            </p>
          </header>

          {read.data.orphanFigures.length > 0 ? (
            <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] leading-relaxed text-ink-300">
              {read.data.orphanFigures.length} extracted figure
              {read.data.orphanFigures.length === 1 ? '' : 's'} could not be matched to a
              section of this document, so they cannot be reviewed here and stay
              unapproved. That is safe - an unreviewed figure is never spoken - but it
              means the document and its figures disagree, which is worth reporting.
            </p>
          ) : null}

          {read.data.chunks.length === 0 ? (
            <p className="text-[13px] text-ink-500">
              This revision has no chunks. A failed parse leaves the document without any.
            </p>
          ) : (
            <ol className="flex flex-col gap-10">
              {read.data.chunks.map((chunk) => (
                <li key={chunk.id} className="flex flex-col gap-4 border-t border-ink-800 pt-5">
                  <div>
                    <p className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
                      Chunk {chunk.ordinal + 1}
                      {chunk.heading === null ? '' : ` · ${chunk.heading}`}
                      {chunk.page_start === null ? '' : ` · page ${chunk.page_start}`}
                      {' · '}
                      {SCOPE_LABELS[chunk.retrieval_scope]}
                    </p>
                    {/* The source text, because scoping a chunk you cannot read
                        is not review either. */}
                    <p className="mt-2 max-w-[80ch] text-[12px] leading-relaxed text-ink-300">
                      {chunk.body}
                    </p>
                  </div>

                  <ChunkScope chunk={chunk} projectIds={projectIds} />

                  <div className="flex flex-col gap-2">
                    <h2 className="text-[11px] tracking-[0.12em] text-ink-500 uppercase">
                      Figures in this chunk
                    </h2>
                    <FigureReview
                      documentId={read.data.id}
                      figures={chunk.figures}
                      chunkScope={chunk.retrieval_scope}
                    />
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </main>
  )
}
