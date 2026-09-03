import { clearedSessionCookie } from '@/lib/admin/session'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/** Ends the session. Unconditional: clearing a cookie nobody has is harmless. */
export async function POST(): Promise<Response> {
  return new Response(null, {
    status: 204,
    headers: { 'set-cookie': clearedSessionCookie(), 'cache-control': 'no-store' },
  })
}
