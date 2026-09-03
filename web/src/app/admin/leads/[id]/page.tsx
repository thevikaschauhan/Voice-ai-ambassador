import Link from 'next/link'
import { headers } from 'next/headers'
import { LeadDetail } from '@/components/admin/lead-detail'
import { readLead } from '@/lib/admin/leads.server'

export const dynamic = 'force-dynamic'

export default async function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const read = await readLead(
    new Request(`https://admin.local/admin/leads/${id}`, {
      headers: { cookie: (await headers()).get('cookie') ?? '' },
    }),
    id,
  )

  return (
    <main className="mx-auto flex min-h-screen max-w-[1000px] flex-col gap-6 px-4 py-6 sm:px-6">
      <Link className="text-[12px] text-ink-400 hover:text-brass-400" href="/admin/leads">
        All leads
      </Link>
      {read.state === 'unauthenticated' ? (
        <p className="border border-ink-700 px-5 py-3.5 text-[13px] text-ink-300">
          <Link className="underline hover:text-brass-400" href="/admin">
            Sign in
          </Link>{' '}
          to see this lead.
        </p>
      ) : read.state === 'unavailable' ? (
        <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300">
          {read.reason}
        </p>
      ) : (
        <LeadDetail lead={read.data} />
      )}
    </main>
  )
}
