import { Banner } from '@astryxdesign/core/Banner'
import { Link } from '@astryxdesign/core/Link'
import { Card } from '@astryxdesign/core/Card'
import { Text } from '@astryxdesign/core/Text'
import { DocumentStatusBadge } from '@/components/admin/status-badge'
import { AdminAppShell } from '@/components/admin/app-shell'
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
    /*
      `heading` is the document's title and `title` stays "Document", for the
      reason recorded on the lead detail: the top bar says what kind of page
      this is, the h1 says which record. This page was the SECOND site shipping
      two level-one headings - my first grep for `<h1` covered
      components/admin/ only and missed app/admin/, so the count I had was one.
    */
    <AdminAppShell
      title="Document"
      heading={read.state === 'ok' ? read.data.title : 'Document'}
    >
      <Link href="/admin/knowledge" hasUnderline>All documents</Link>

      {read.state === 'unauthenticated' ? (
        <Banner
          status="info"
          title={
            <>
              <Link href="/admin" hasUnderline>Sign in</Link> to review this document.
            </>
          }
        />
      ) : read.state === 'unavailable' ? (
        <Banner status="error" title={read.reason} />
      ) : (
        <>
          {/* No h1: the shell renders the document title as the page h1. */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Text as="span" type="supporting" color="secondary">
              {read.data.source_type.toUpperCase()} · revision {read.data.revision}
            </Text>
            <DocumentStatusBadge status={read.data.status} />
          </div>

          {read.data.orphanFigures.length > 0 ? (
            <Banner
              status="warning"
              title={`${read.data.orphanFigures.length} extracted figure${
                read.data.orphanFigures.length === 1 ? '' : 's'
              } could not be matched to a section`}
              description={`${read.data.orphanFigures.length} extracted figure
${
                read.data.orphanFigures.length === 1 ? '' : 's'
              } could not be matched to a section of this document, so they cannot be reviewed here and stay unapproved. That is safe - an unreviewed figure is never spoken - but it means the document and its figures disagree, which is worth reporting.`}
            />
          ) : null}

          {read.data.chunks.length === 0 ? (
            <Text as="p" display="block" color="secondary">
              This revision has no chunks. A failed parse leaves the document without any.
            </Text>
          ) : (
            <ol className="flex flex-col gap-10">
              {read.data.chunks.map((chunk) => (
                <li key={chunk.id}>
                  <Card padding={4}>
                   <div className="flex flex-col gap-4">
                  <div className="flex flex-col gap-2">
                    <Text as="h2" type="large" weight="semibold">
                      Chunk {chunk.ordinal + 1}
                      {chunk.heading === null ? '' : ` · ${chunk.heading}`}
                      {chunk.page_start === null ? '' : ` · page ${chunk.page_start}`}
                      {' · '}
                      {SCOPE_LABELS[chunk.retrieval_scope]}
                    </Text>
                    {/* The source text, because scoping a chunk you cannot read
                        is not review either. */}
                    <Text as="p" display="block">
                      {chunk.body}
                    </Text>
                  </div>

                  <ChunkScope chunk={chunk} projectIds={projectIds} />

                  <div className="flex flex-col gap-2">
                    {/* h3, under the chunk's own h2: the outline is
                        page title > chunk > figures. */}
                    <Text as="h3" weight="semibold">
                      Figures in this chunk
                    </Text>
                    <FigureReview
                      documentId={read.data.id}
                      figures={chunk.figures}
                      chunkScope={chunk.retrieval_scope}
                    />
                  </div>
                   </div>
                  </Card>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </AdminAppShell>
  )
}
