import Link from 'next/link'
import { DocumentList } from '@/components/admin/document-list'
import { KnowledgeIntake } from '@/components/admin/knowledge-intake'
import { readForPage } from '@/lib/admin/read'
import type { DocumentRow } from '@/lib/admin/knowledge'

export const dynamic = 'force-dynamic'

/** What the ambassador may draw on, and how a document gets here. */
export default async function KnowledgePage() {
  const read = await readForPage<{ documents: DocumentRow[] }>({ route: 'documents' })

  return (
    <main className="mx-auto flex min-h-screen max-w-[1080px] flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
        <div>
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">Knowledge</h1>
          <p className="mt-1.5 max-w-[76ch] text-[12px] leading-relaxed text-ink-500">
            Documents the ambassador may draw on. Nothing here reaches a call until its
            chunks have been scoped and its figures approved one occurrence at a time.
          </p>
        </div>
        <Link className="text-[12px] text-ink-400 hover:text-brass-400" href="/admin">
          Admin
        </Link>
      </header>

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
            <DocumentList rows={read.data.documents} />
          )}
        </>
      )}
    </main>
  )
}
