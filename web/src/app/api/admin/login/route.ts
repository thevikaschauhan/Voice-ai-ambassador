import { checkAdminCode } from '@/lib/admin/gate'
import { sessionCookie, signAdminSession } from '@/lib/admin/session'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * The admin door.
 *
 * POST, because it creates a session. The three failure reasons - no code
 * configured, wrong code, too many attempts - give the caller two answers
 * between them: 403 or 429. Which of the two 403s it was goes to the server
 * log, where an operator can act on it; telling the caller that no code is
 * configured tells them something useful about a service they cannot use.
 */
export async function POST(request: Request): Promise<Response> {
  let code: unknown
  try {
    code = ((await request.json()) as { code?: unknown }).code
  } catch {
    code = undefined
  }

  const result = checkAdminCode(code)
  if (result === 'rate_limited') {
    return Response.json(
      { error: 'too many attempts; wait a few minutes' },
      { status: 429, headers: { 'cache-control': 'no-store' } },
    )
  }
  if (result !== 'ok') {
    console.warn(
      result === 'closed'
        ? 'admin: refused because ADMIN_ACCESS_CODE is not set; an unset gate is a closed gate'
        : 'admin: refused an attempt with the wrong access code',
    )
    return Response.json(
      { error: 'that access code was not accepted' },
      { status: 403, headers: { 'cache-control': 'no-store' } },
    )
  }

  let cookie: string
  try {
    cookie = sessionCookie(signAdminSession({ issuedAt: Date.now() }))
  } catch (error) {
    // A configured code with no session secret cannot mint a session, and
    // minting an unsigned one would be worse than refusing.
    console.error(`admin: cannot sign a session: ${error instanceof Error ? error.message : ''}`)
    return Response.json(
      { error: 'this deployment cannot sign an admin session' },
      { status: 503, headers: { 'cache-control': 'no-store' } },
    )
  }

  // 204: the session is the cookie, and there is nothing to say in a body.
  return new Response(null, {
    status: 204,
    headers: { 'set-cookie': cookie, 'cache-control': 'no-store' },
  })
}
