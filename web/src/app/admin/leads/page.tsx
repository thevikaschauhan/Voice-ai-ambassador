import Link from 'next/link'
import { headers } from 'next/headers'
import { AdminAppShell } from '@/components/admin/app-shell'
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
    <AdminAppShell title="Leads">
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
    </AdminAppShell>
  )
}
