import { headers } from 'next/headers'
import { AdminShell } from '@/components/admin-shell'
import { AdminAppShell } from '@/components/admin/app-shell'
import { OverviewCards } from '@/components/admin/overview-cards'
import { adminCodeConfigured } from '@/lib/admin/gate'
import { readDocumentRows } from '@/lib/admin/knowledge.server'
import { readLeadRows } from '@/lib/admin/leads.server'
import { readAdminSession } from '@/lib/admin/session'

export const dynamic = 'force-dynamic'

/**
 * The admin overview (ADR-021, and task-web-admin-astryx-shell).
 *
 * Whether the visitor is signed in is decided HERE rather than in the browser,
 * so an unauthenticated page never renders a nav that implies data behind it -
 * which is also why the app shell lives inside the signed-in branch and the
 * sign-in screen stands alone.
 *
 * It passes no secret and no fact about one. `configured` says only whether an
 * access code exists, which decides between "sign in" and "this deployment has
 * no admin access configured": an operator needs to tell those apart, and
 * neither reveals anything a guesser could use.
 *
 * The counts come from the two list reads the other pages already do, so there
 * is no count endpoint to add and no second source of truth for a number.
 */
export default async function AdminPage() {
  const cookie = (await headers()).get('cookie')
  if (readAdminSession(cookie) === null) {
    return <AdminShell configured={adminCodeConfigured()} />
  }

  const request = new Request('https://admin.local/admin', {
    headers: { cookie: cookie ?? '' },
  })
  const [leads, documents] = await Promise.all([
    readLeadRows(request),
    readDocumentRows(request),
  ])

  return (
    <AdminAppShell title="Overview">
      <div className="flex flex-col gap-6">
        <p className="max-w-[74ch] text-sm text-secondary">
          Leads and the ambassador&rsquo;s knowledge base. Nothing a document says
          reaches a call until its chunks are scoped and its figures approved.
        </p>
        <OverviewCards
          leads={leads.state === 'ok' ? leads.data : []}
          documents={documents.state === 'ok' ? documents.data : []}
        />
        {leads.state === 'unavailable' || documents.state === 'unavailable' ? (
          <p className="text-sm text-error" role="status">
            {leads.state === 'unavailable' ? leads.reason : null}
            {documents.state === 'unavailable' ? documents.reason : null}
          </p>
        ) : null}
      </div>
    </AdminAppShell>
  )
}
