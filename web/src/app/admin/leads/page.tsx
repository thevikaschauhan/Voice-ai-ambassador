import Link from 'next/link'
import { headers } from 'next/headers'
import { LeadList } from '@/components/admin/lead-list'
import { readLeadRows } from '@/lib/admin/leads.server'

export const dynamic = 'force-dynamic'

/** Every call that finished, newest first (the API's order). */
export default async function LeadsPage() {
  const read = await readLeadRows(
    new Request('https://admin.local/admin/leads', {
      headers: { cookie: (await headers()).get('cookie') ?? '' },
    }),
  )

  return (
    <main className="mx-auto flex min-h-screen max-w-[1180px] flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
        <div>
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">Leads</h1>
          <p className="mt-1.5 max-w-[74ch] text-[12px] leading-relaxed text-ink-500">
            Every call that finished, including the ones that were cut short. Buyer words
            and contact details are on the detail page, not here.
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
          to see leads.
        </p>
      ) : read.state === 'unavailable' ? (
        <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300">
          {read.reason}
        </p>
      ) : (
        <LeadList rows={read.data} />
      )}
    </main>
  )
}
