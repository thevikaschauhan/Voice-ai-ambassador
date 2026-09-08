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

describe('an arrival says whether it is a prefetch, because an arrival is not a render', () => {
  /**
   * WHY THIS FIELD EXISTS, from a measurement (task-web-request-log-prefetch-flag).
   *
   * On 2026-09-07 a visit produced SIXTEEN arrivals at /admin/knowledge/<id>
   * and not one document read, on either side of the wire. That looked like a
   * defect in the detail page until the leads route answered it: three arrivals
   * at /admin/leads/<id>, of which the first two also read nothing and the
   * third read normally and reached admin-api. So an arrival at a dynamic route
   * does not imply a render, and the page was never run rather than running
   * without fetching.
   *
   * `web_request` records method and pathname only, which makes a prefetch and
   * a navigation IDENTICAL in the log - the sixteen and the one look the same.
   * Without this flag every count of "page views" is wrong by the prefetch
   * factor, which that day was 16 to 0.
   *
   * THE HEADER EXCEPTION, written beside the rule it narrows. `request-log.ts`
   * says no header is ever recorded. This narrows that rule and nothing else:
   * two header NAMES are tested for presence, the boolean that results is
   * logged, and the VALUE is never read into the line. Recording a derived
   * boolean is not recording the header. Case (e) plants the marker in the
   * header's own value to hold that line.
   *
   * Names are Next's own (16.3.4, client/components/app-router-headers.js):
   * `next-router-prefetch` and `next-router-segment-prefetch`. `rsc` marks ANY
   * RSC request, navigations included, so it is NOT the discriminator - case
   * (d) is the control that fails if someone reaches for it.
   */
  async function arrive(headers: Record<string, string>): Promise<Record<string, unknown>[]> {
    const lines = captureLines()
    const { middleware } = await import('@/middleware')
    const { NextRequest } = await import('next/server')
    middleware(new NextRequest('https://demo.example/admin/knowledge/some-id', { headers }))
    return lines.map((l) => JSON.parse(l) as Record<string, unknown>)
  }

  it('flags an arrival carrying next-router-prefetch', async () => {
    const [line] = await arrive({ 'next-router-prefetch': '1' })
    expect(line.event).toBe('web_request')
    expect(line.prefetch).toBe(true)
  })

  it('flags an arrival carrying next-router-segment-prefetch', async () => {
    const [line] = await arrive({ 'next-router-segment-prefetch': '/_tree' })
    expect(line.prefetch).toBe(true)
  })

  it('records false, not absent, when neither header is present', async () => {
    // `false` and "the field is missing" are different claims: a missing field
    // is how a line written before this shipped looks, and a count that treats
    // the two alike silently folds old lines into "navigation".
    const [line] = await arrive({})
    expect(line.prefetch).toBe(false)
    expect('prefetch' in line).toBe(true)
  })

  it('does NOT flag an rsc request, which is any RSC fetch including a navigation', async () => {
    const [line] = await arrive({ rsc: '1' })
    expect(line.prefetch).toBe(false)
  })

  it('carries no header value, with the marker planted in the header itself', async () => {
    const lines = await arrive({
      'next-router-prefetch': `${MARKER}-header`,
      cookie: `admin_session=${MARKER}-cookie`,
    })
    // Assert the line EXISTS before asserting what it lacks: `not.toContain`
    // over an empty array passes for the wrong reason, and this is the case
    // that would be silently vacuous if the emitter ever went quiet.
    expect(lines).toHaveLength(1)
    expect(lines[0].prefetch).toBe(true)
    expect(JSON.stringify(lines)).not.toContain(MARKER)
  })

  it('carries no key beyond the ones it is specified to carry', async () => {
    // A future header cannot ride in unnoticed: the shape is pinned, not
    // merely checked for the fields we happen to want today.
    const [line] = await arrive({ 'next-router-prefetch': '1' })
    expect(Object.keys(line).sort()).toEqual(
      ['event', 'level', 'method', 'path', 'prefetch', 'ts'].sort(),
    )
  })
})

describe('a page read writes its own line, and not the proxy\'s', () => {
  /**
   * WHY A THIRD EVENT RATHER THAN A WIDER `admin_proxy` (task-web-request-log-page-reads).
   *
   * Measured on deployment 12ea951e's window: web logged 43 `web_request` and
   * ONE `admin_proxy` while admin-api logged about ten GETs. Not a bug in the
   * emitter - a boundary nobody had noticed. `read.ts` says in its own
   * docstring that a server component calls `forward` directly rather than
   * fetching its own `/api/admin/*` proxy, so a page read never passes
   * `proxy()`, which is where `admin_proxy` is written. Page reads had an
   * arrival line and no outcome line: no status, no duration, no route.
   *
   * The fix is a THIRD event, not a wider second one. `admin_proxy` keeps
   * meaning exactly what it has meant since it shipped, so a count by event
   * stays comparable across the log's whole history - including the 12ea951e
   * window already read and reported.
   */

  it('emits exactly one admin_page_read carrying ts, method, route, status and duration', async () => {
    upstreamAnswers(200, { documents: [] })
    const lines = captureLines()

    const { readForPage } = await import('@/lib/admin/read')
    const read = await readForPage(await validCookie(), { route: 'documents' })
    expect(read.state).toBe('ok')

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('admin_page_read')
    expect(line.method).toBe('GET')
    expect(line.route).toBe('documents')
    expect(line.status).toBe(200)
    expect(typeof line.duration_ms).toBe('number')
    expect(line.duration_ms as number).toBeGreaterThanOrEqual(0)
    expect(new Date(line.ts as string).toISOString()).toBe(line.ts)
  })

  it('is NOT an admin_proxy line, so counts by event stay comparable across the log history', async () => {
    // The explicit constraint on this card: widening `admin_proxy` would have
    // silently rewritten the meaning of every count already taken from it.
    upstreamAnswers(200, { documents: [] })
    const lines = captureLines()

    const { readForPage } = await import('@/lib/admin/read')
    await readForPage(await validCookie(), { route: 'documents' })

    expect(lines).toHaveLength(1)
    expect(lines.join('\n')).toContain('admin_page_read')
    expect(lines.join('\n')).not.toContain('"event":"admin_proxy"')
  })

  it('carries no cookie, no query string and no body, with all three planted', async () => {
    upstreamAnswers(200, { documents: [] })
    const lines = captureLines()

    const { readForPage } = await import('@/lib/admin/read')
    const cookie = `${await validCookie()}; tracking=${MARKER}-cookie`
    await readForPage(cookie, {
      route: 'documents',
      search: `?project=${MARKER}-query`,
      body: { note: `${MARKER}-body` },
    })

    // The existence precondition, for the reason recorded on the first leak
    // case in this file: with nothing logged, "contains no marker" is vacuously
    // true and the case cannot fail.
    expect(lines).toHaveLength(1)
    const everything = lines.join('\n')
    expect(everything).not.toContain(MARKER)
    expect(everything).not.toContain(cookie.split('=')[1].split(';')[0])
    expect(everything).not.toContain(TOKEN)
    expect(everything).not.toContain(UPSTREAM)
  })

  it('reports the upstream status it was answered with, not the state it returns', async () => {
    // `readForPage` maps a 404 to state 'unavailable' with prose. The LOG must
    // carry 404: "no such document" and "the API is broken" are the same state
    // to a page and different diagnoses to whoever reads the log.
    upstreamAnswers(404, { detail: 'nope' })
    const lines = captureLines()

    const { readForPage } = await import('@/lib/admin/read')
    const read = await readForPage(await validCookie(), { route: 'document', id: 'abc' })
    expect(read.state).toBe('unavailable')

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('admin_page_read')
    expect(line.status).toBe(404)
    expect(line.route).toBe('document')
  })

  it('writes a line for a refusal too, where no upstream call happens at all', async () => {
    // Same property as the proxy's refusal case: an absence in the log must
    // mean no read happened, never "the read was refused at the door".
    const lines = captureLines()

    const { readForPage } = await import('@/lib/admin/read')
    const read = await readForPage(null, { route: 'documents' })
    expect(read.state).toBe('unauthenticated')

    expect(lines).toHaveLength(1)
    const line = JSON.parse(lines[0]) as Record<string, unknown>
    expect(line.event).toBe('admin_page_read')
    expect(line.status).toBe(401)
    expect(line.route).toBe('documents')
  })
})
