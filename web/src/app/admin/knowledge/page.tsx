import Link from 'next/link'
import { Banner } from '@astryxdesign/core/Banner'
import { DocumentList } from '@/components/admin/document-list'
import { KnowledgeIntake } from '@/components/admin/knowledge-intake'
import { headers } from 'next/headers'
import { AdminAppShell } from '@/components/admin/app-shell'
import { readDocumentRows } from '@/lib/admin/knowledge.server'

export const dynamic = 'force-dynamic'

/** What the ambassador may draw on, and how a document gets here. */
export default async function KnowledgePage() {
  const read = await readDocumentRows(
    new Request('https://admin.local/admin/knowledge', {
      headers: { cookie: (await headers()).get('cookie') ?? '' },
    }),
  )

  return (
    <AdminAppShell title="Knowledge">
      {read.state === 'unauthenticated' ? (
        <Banner
          status="info"
          title={
            <>
              <Link href="/admin">Sign in</Link> to review knowledge.
            </>
          }
        />
      ) : (
        <>
          <KnowledgeIntake />
          {read.state === 'unavailable' ? (
            <Banner status="error" title={read.reason} />
          ) : (
            <DocumentList rows={read.data} />
          )}
        </>
      )}
    </AdminAppShell>
  )
}
