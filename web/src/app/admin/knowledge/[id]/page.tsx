import { Banner } from '@astryxdesign/core/Banner'
import { Link } from '@astryxdesign/core/Link'
import { Text } from '@astryxdesign/core/Text'
import { DocumentStatusBadge } from '@/components/admin/status-badge'
import { sourceLabel } from '@/lib/admin/knowledge'
import { AdminAppShell } from '@/components/admin/app-shell'
import { DocumentReview } from '@/components/admin/document-review'
import { headers } from 'next/headers'
import { loadInventory } from '@/lib/inventory'
import { readDocument } from '@/lib/admin/knowledge.server'

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
          {/*
            THE HEADER BLOCK (finding G7), matching the lead detail's: what
            this document is, where it came from and where the review stands,
            in one place with a rule under it.

            The source is SPELLED here, not uppercased. PR E spelled it on the
            list and left this header printing the raw enum, so "PASTE" was
            still on screen one click away - the same one-surface miss that
            left a warning-yellow badge on the lead detail. Both now read from
            the one map in lib/admin/knowledge.

            No h1: the shell renders the document title as the page h1.
          */}
          <div
            data-testid="document-header"
            className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] pb-4"
          >
            <Text as="span" type="supporting" color="secondary">
              {sourceLabel(read.data.source_type)} · revision {read.data.revision}
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

          <DocumentReview
            key={`${read.data.id}-${read.data.revision}`}
            document={read.data}
            projects={projects.map(p => ({id: p.id, name: p.name}))}
          />
        </>
      )}
    </AdminAppShell>
  )
}
