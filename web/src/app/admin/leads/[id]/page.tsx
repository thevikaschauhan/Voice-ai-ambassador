import Link from 'next/link'
import { Banner } from '@astryxdesign/core/Banner'
import { AdminAppShell } from '@/components/admin/app-shell'
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
    /*
      `heading` is the session id and `title` stays "Lead": the top bar says
      what kind of page this is, the h1 says which record. Passing only one
      string here meant either a useless h1 or a second one from the component,
      and this page had the second one.
    */
    <AdminAppShell title="Lead" heading={read.state === 'ok' ? read.data.session_id : 'Lead'}>
      <Link href="/admin/leads">All leads</Link>
      {read.state === 'unauthenticated' ? (
        /* Banner's prop is `status`, not variant, and `title` is required -
           it renders role=status for info/success and role=alert for
           error/warning, so the message keeps a live region either way. */
        <Banner
          status="info"
          title={
            <>
              <Link href="/admin">Sign in</Link> to see this lead.
            </>
          }
        />
      ) : read.state === 'unavailable' ? (
        <Banner status="error" title={read.reason} />
      ) : (
        <LeadDetail lead={read.data} />
      )}
    </AdminAppShell>
  )
}
