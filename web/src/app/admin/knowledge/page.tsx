import Link from 'next/link'
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
        <p className="border border-ink-700 px-5 py-3.5 text-[13px] text-ink-300">
          <Link className="underline hover:text-brass-400" href="/admin">
            Sign in
          </Link>{' '}
          to review knowledge.
        </p>
      ) : (
        <>
          <KnowledgeIntake />
          {read.state === 'unavailable' ? (
            <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300">
              {read.reason}
            </p>
          ) : (
            <DocumentList rows={read.data} />
          )}
        </>
      )}
    </AdminAppShell>
  )
}
