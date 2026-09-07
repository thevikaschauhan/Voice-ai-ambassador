import { Banner } from '@astryxdesign/core/Banner'
import { Link } from '@astryxdesign/core/Link'
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
              <Link href="/admin" hasUnderline>Sign in</Link> to review knowledge.
            </>
          }
        />
      ) : (
        <div className="flex flex-col gap-4">
          {/*
            THE LIST FIRST (finding G6: "the intake form dominates the top ...
            pushing the list down"). A reviewer opens this page to READ the
            library far more often than to add to it, and it used to open with
            a five-row textarea and a full-width submit above the first
            document. Adding is now a panel behind one button, and the button
            sits above the list because that is where a reviewer looks for it -
            the FORM being below the fold is fine, the LIST being below it was
            not.
          */}
          <KnowledgeIntake />
          {read.state === 'unavailable' ? (
            <Banner status="error" title={read.reason} />
          ) : (
            <DocumentList rows={read.data} />
          )}
        </div>
      )}
    </AdminAppShell>
  )
}
