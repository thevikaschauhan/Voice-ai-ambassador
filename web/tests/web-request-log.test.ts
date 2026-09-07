// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The web service's request log (task-web-request-log).
 *
 * WHY THIS EXISTS, from a measurement rather than a hunch: on 2026-09-07 the
 * question "did the knowledge intake ever submit anything?" could not be
 * answered from web's own log, because `next start` prints no access log. Its
 * production log was ten startup lines and nothing since. The proof that this
 * was a GAP and not silence was a proxied `GET /v1/knowledge/documents` sitting
 * in admin-api's log at 04:52:18Z: a request that certainly passed through web
 * and left no trace in it. A zero from an instrument that has never been seen
 * writing anything is not evidence of absence.
 *
 * So: one line when a request arrives, one line when a proxied call completes.
 *
 * The negative half is the half that can hurt someone. A request log is the
 * easiest place in a codebase to leak a session cookie, a search term or a
 * document body, because the natural implementation logs "the request" and a
 * Request carries all three. These tests plant the marker NOTAREAL in the
 * cookie, the query string and the body of one request and assert it appears
 * in no emitted line - so the leak is caught by a grep over the real output,
 * not by reading the emitter and believing it.
 */

const SECRET = 'a-session-secret-long-enough-for-hmac-sha256'
const TOKEN = 'upstream-bearer-token-value'
const UPSTREAM = 'http://admin-api.railway.internal:8000'

/** Planted in cookie, query and body; must survive into no log line. */
const MARKER = 'NOTAREAL'

/** Captures every line the code under test writes to stdout. */
function captureLines(): string[] {
  const lines: string[] = []
  vi.spyOn(console, 'log').mockImplementation((...args: unknown[]) => {
    lines.push(args.map(String).join(' '))
  })
  return lines
}

async function validCookie(): Promise<string> {
  const { signAdminSession } = await import('@/lib/admin/session')
  return `admin_session=${signAdminSession({ issuedAt: Date.now() })}`
}

function upstreamAnswers(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    (async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      })) as typeof fetch,
  )
}

beforeEach(() => {
  vi.resetModules()
  process.env.ADMIN_SESSION_SECRET = SECRET
  process.env.ADMIN_API_TOKEN = TOKEN
  process.env.ADMIN_API_URL = UPSTREAM
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  delete process.env.ADMIN_SESSION_SECRET
  delete process.env.ADMIN_API_TOKEN
  delete process.env.ADMIN_API_URL
})

describe('the admin proxy writes one line per completed call', () => {
  it('emits exactly one line carrying ts, method, route, status and duration', async () => {
    upstreamAnswers(200, { leads: [] })
    const lines = captureLines()

    const { GET } = await import('@/app/api/admin/leads/route')
    const response = await GET(
      new Request('https://demo.example/api/admin/leads', {
        headers: { cookie: await validCookie() },
      }),
    )
    expect(response.status).toBe(200)

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('admin_proxy')
    expect(line.method).toBe('GET')
    // The route NAME from the fixed upstream table, not a pathname: the table
    // is closed, so this field cannot carry an id the caller chose.
    expect(line.route).toBe('leads')
    expect(line.status).toBe(200)
    expect(typeof line.duration_ms).toBe('number')
    expect(line.duration_ms as number).toBeGreaterThanOrEqual(0)
    expect(typeof line.ts).toBe('string')
    expect(new Date(line.ts as string).toISOString()).toBe(line.ts)
  })

  it('carries no cookie, no query string and no body, with all three planted', async () => {
    upstreamAnswers(200, { leads: [] })
    const lines = captureLines()

    const { GET } = await import('@/app/api/admin/leads/route')
    const cookie = `${await validCookie()}; tracking=${MARKER}-cookie`
    const response = await GET(
      new Request(`https://demo.example/api/admin/leads?project=${MARKER}-query`, {
        headers: { cookie },
      }),
    )
    expect(response.status).toBe(200)

    // ASSERT THE LINE EXISTS BEFORE ASSERTING WHAT IT LACKS. Without this the
    // case passes on an empty array - green today because nothing is logged at
    // all, and green forever if the emitter ever regressed to silence. A leak
    // test that cannot tell "clean" from "absent" is not a leak test.
    expect(lines).toHaveLength(1)

    // Every line, flattened: a leak in any of them is a leak.
    const everything = lines.join('\n')
    expect(everything).not.toContain(MARKER)
    // And the session token itself is not a marker we planted, so assert it
    // separately - it is the value an attacker actually wants.
    expect(everything).not.toContain(cookie.split('=')[1].split(';')[0])
    expect(everything).not.toContain(TOKEN)
    expect(everything).not.toContain(UPSTREAM)
  })

  it('writes a line for a refusal too, so a rejected request is still countable', async () => {
    // The case a success-only emitter would miss, and the reason the log
    // exists: "nothing reached the server" and "everything was refused at the
    // door" are different diagnoses and must not look identical.
    const lines = captureLines()

    const { GET } = await import('@/app/api/admin/leads/route')
    const response = await GET(new Request('https://demo.example/api/admin/leads'))
    expect(response.status).toBe(401)

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('admin_proxy')
    expect(line.status).toBe(401)
    expect(line.route).toBe('leads')
  })

  it('reports the upstream status it actually got, not a status it hoped for', async () => {
    upstreamAnswers(422, { detail: 'nope' })
    const lines = captureLines()

    const { POST } = await import('@/app/api/admin/knowledge/documents/route')
    const response = await POST(
      new Request('https://demo.example/api/admin/knowledge/documents', {
        method: 'POST',
        headers: {
          cookie: await validCookie(),
          origin: 'https://demo.example',
          host: 'demo.example',
          'content-type': 'application/json',
        },
        body: JSON.stringify({ source_type: 'paste', title: MARKER, text: `${MARKER}-body` }),
      }),
    )
    expect(response.status).toBe(422)

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.status).toBe(422)
    expect(line.method).toBe('POST')
    expect(line.route).toBe('documents')
    // The title and the pasted text are document content; neither belongs here.
    expect(lines.join('\n')).not.toContain(MARKER)
  })
})

describe('middleware writes one line per admin request that arrives', () => {
  it('emits method and pathname for an /admin page, with the query dropped', async () => {
    const lines = captureLines()

    const { middleware } = await import('@/middleware')
    const { NextRequest } = await import('next/server')
    middleware(
      new NextRequest(`https://demo.example/admin/knowledge?q=${MARKER}-query`, {
        headers: { cookie: `admin_session=${MARKER}-cookie` },
      }),
    )

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('web_request')
    expect(line.method).toBe('GET')
    expect(line.path).toBe('/admin/knowledge')
    expect(typeof line.ts).toBe('string')
    expect(lines.join('\n')).not.toContain(MARKER)
  })

  it('matches /admin and /api/admin and nothing else', async () => {
    // The matcher is the scope of the whole feature: too narrow and the gap
    // stays open on the routes we care about, too wide and every public page
    // view is logged.
    const { config } = await import('@/middleware')
    const matcher = config.matcher as string[]
    expect(matcher.some((m) => m.startsWith('/admin'))).toBe(true)
    expect(matcher.some((m) => m.startsWith('/api/admin'))).toBe(true)
    expect(matcher.every((m) => m.startsWith('/admin') || m.startsWith('/api/admin'))).toBe(true)
  })
})
