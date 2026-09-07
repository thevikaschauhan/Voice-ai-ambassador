import { Banner } from '@astryxdesign/core/Banner'
import { Link } from '@astryxdesign/core/Link'
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
        <Banner
          status="info"
          title={
            <>
              <Link href="/admin" hasUnderline>Sign in</Link> to see leads.
            </>
          }
        />
      ) : read.state === 'unavailable' ? (
        <Banner status="error" title={read.reason} />
      ) : (
        <LeadList rows={read.data} />
      )}
    </AdminAppShell>
  )
}
