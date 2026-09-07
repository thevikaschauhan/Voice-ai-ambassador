import { Banner } from '@astryxdesign/core/Banner'
import { Link } from '@astryxdesign/core/Link'
import { headers } from 'next/headers'
import { AdminAppShell } from '@/components/admin/app-shell'
import { LeadFilterChips } from '@/components/admin/lead-filters'
import { LeadList } from '@/components/admin/lead-list'
import { readLeadRows } from '@/lib/admin/leads.server'

export const dynamic = 'force-dynamic'

/**
 * Every call that finished, newest first (the API's order), narrowed by the
 * status in the URL.
 *
 * THE QUERY STRING IS THE FILTER, and until this change the page never passed
 * one. `readLeadRows` reads `new URL(request.url).search` and forwards it, and
 * the Request built here carried a hardcoded path with no search at all - so
 * `list_leads`'s status, language and project_id filters had been unreachable
 * from this tier since they were written. The whitelist in
 * `leads.server.ts` decides what actually reaches the API; this page's job is
 * only to stop discarding what the reviewer asked for.
 *
 * `searchParams` is awaited because it is a Promise in this version of the App
 * Router, and reading it is what makes the route dynamic - which it already is
 * by declaration above.
 */
export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const params = await searchParams
  const status = typeof params.status === 'string' ? params.status : null

  const query = new URLSearchParams()
  // Only what the reviewer chose. The filter's own validity is judged by
  // `forwardedSearch`, so a hand-edited value is dropped there rather than
  // being second-guessed in two places.
  if (status !== null) query.set('status', status)
  const search = query.toString()

  const read = await readLeadRows(
    new Request(
      `https://admin.local/admin/leads${search === '' ? '' : `?${search}`}`,
      { headers: { cookie: (await headers()).get('cookie') ?? '' } },
    ),
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
        <div className="flex flex-col gap-4">
          {/*
            The chips above the list, so the filter and the count it produced
            read as one row. `status` is passed through unvalidated on purpose:
            if a hand-edited URL names something the API does not accept, the
            read drops it and every chip shows as inactive - which is the
            honest rendering of "this filter matched nothing I understand",
            and better than silently marking All while the list is unfiltered.
          */}
          <LeadFilterChips active={status} />
          <LeadList rows={read.data} />
        </div>
      )}
    </AdminAppShell>
  )
}
