import { AdminShell } from '@/components/admin-shell'
import { adminCodeConfigured } from '@/lib/admin/gate'
import { readAdminSession } from '@/lib/admin/session'
import { headers } from 'next/headers'

export const dynamic = 'force-dynamic'

/**
 * The admin surface, which is a thin protected shell (ADR-021).
 *
 * The domain lives in the Python admin API; this tier authenticates, proxies
 * and renders. Whether the visitor is signed in is decided HERE rather than in
 * the browser, so an unauthenticated page never renders a nav that implies
 * data behind it.
 *
 * It passes no secret and no fact about one - not the upstream address, not
 * whether a token is set. `configured` says only whether an access code
 * exists, which decides between "sign in" and "this deployment has no admin
 * access configured": an operator needs to tell those apart, and neither
 * reveals anything a guesser could use.
 */
export default async function AdminPage() {
  const cookie = (await headers()).get('cookie')
  return (
    <AdminShell
      signedIn={readAdminSession(cookie) !== null}
      configured={adminCodeConfigured()}
    />
  )
}
