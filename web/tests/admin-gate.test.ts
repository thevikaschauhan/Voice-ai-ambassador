// @vitest-environment node
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The `/admin` gate and its proxy, tested before either exists (P2-S11).
 *
 * The surface contract is `docs/10-admin.md`: an unset-closed, rate-limited,
 * constant-time code check; a short-lived signed session cookie; and FIXED
 * same-origin proxy routes that add the upstream bearer token server-side. The
 * property that matters more than any of them is negative - the browser must
 * never receive `ADMIN_API_TOKEN`, and must never be able to choose an upstream
 * address and turn the proxy into an open relay.
 *
 * These are written against the routes rather than the helpers, because the
 * claim is about what a request gets back, not about what a function returns.
 */

const CODE = 'an-admin-code-long-enough'
const SECRET = 'a-session-secret-long-enough-for-hmac-sha256'
const TOKEN = 'upstream-bearer-token-value'
const UPSTREAM = 'http://admin-api.railway.internal:8000'

const SRC = join(process.cwd(), 'src')

function sources(dir = SRC): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(entry) ? [path] : []
  })
}

/** Everything a route handed back, flattened, so a leak anywhere is a leak. */
async function everythingSent(response: Response): Promise<string> {
  const headers = [...response.headers.entries()].map(([k, v]) => `${k}: ${v}`).join('\n')
  return `${headers}\n${await response.text()}`
}

function post(url: string, body: unknown, headers: Record<string, string> = {}): Request {
  return new Request(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', origin: 'https://demo.example', ...headers },
    body: JSON.stringify(body),
  })
}

beforeEach(() => {
  vi.resetModules()
  delete process.env.ADMIN_ACCESS_CODE
  delete process.env.ADMIN_SESSION_SECRET
  delete process.env.ADMIN_API_TOKEN
  delete process.env.ADMIN_API_URL
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  delete process.env.ADMIN_ACCESS_CODE
  delete process.env.ADMIN_SESSION_SECRET
  delete process.env.ADMIN_API_TOKEN
  delete process.env.ADMIN_API_URL
})

describe('the admin gate', () => {
  it('admin stays closed when ADMIN_ACCESS_CODE is absent and never serialises the upstream token', async () => {
    // The ships row's RED test (docs/06-, P2-S11). Both halves in one case
    // because they are one property: a closed door that leaks the key is not
    // closed.
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM

    const { POST } = await import('@/app/api/admin/login/route')
    const refused = await POST(post('https://demo.example/api/admin/login', { code: CODE }))
    expect(refused.status).toBe(403)

    const sent = await everythingSent(refused)
    expect(sent).not.toContain(TOKEN)
    expect(sent).not.toContain(UPSTREAM)
    expect(sent.toLowerCase()).not.toContain('set-cookie')
  })

  it('refuses the wrong code with 403 and sets no session', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    const { POST } = await import('@/app/api/admin/login/route')
    const response = await POST(post('https://demo.example/api/admin/login', { code: 'wrong' }))
    expect(response.status).toBe(403)
    expect(response.headers.get('set-cookie')).toBeNull()
  })

  it('accepts the right code and sets a HttpOnly, Secure, SameSite=Strict cookie', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    const { POST } = await import('@/app/api/admin/login/route')
    const response = await POST(post('https://demo.example/api/admin/login', { code: CODE }))
    expect(response.status).toBe(204)
    const cookie = response.headers.get('set-cookie') ?? ''
    expect(cookie).toMatch(/HttpOnly/i)
    expect(cookie).toMatch(/Secure/i)
    expect(cookie).toMatch(/SameSite=Strict/i)
    // Short-lived: a session that outlives the demo is a session somebody
    // else's laptop still has.
    expect(cookie).toMatch(/Max-Age=(\d+)/i)
    const maxAge = Number(/Max-Age=(\d+)/i.exec(cookie)?.[1] ?? 0)
    expect(maxAge).toBeGreaterThan(0)
    expect(maxAge).toBeLessThanOrEqual(60 * 60 * 8)
  })

  it('rate-limits repeated wrong codes with 429 rather than answering forever', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    const { POST } = await import('@/app/api/admin/login/route')
    const statuses: number[] = []
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const response = await POST(post('https://demo.example/api/admin/login', { code: 'wrong' }))
      statuses.push(response.status)
    }
    // A public URL in front of a shared code is a guessing surface, so the
    // door has to stop answering.
    expect(statuses).toContain(429)
    expect(statuses.at(-1)).toBe(429)
  })
})

describe('the admin proxy', () => {
  async function validCookie(): Promise<string> {
    const { POST } = await import('@/app/api/admin/login/route')
    const response = await POST(post('https://demo.example/api/admin/login', { code: CODE }))
    const cookie = response.headers.get('set-cookie') ?? ''
    return cookie.split(';')[0]
  }

  it('forwards a valid session to the upstream with the bearer added server-side', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM

    const seen: { url: string; auth: string | null }[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers)
        seen.push({ url: String(input), auth: headers.get('authorization') })
        return new Response(JSON.stringify({ leads: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }) as typeof fetch,
    )

    const cookie = await validCookie()
    const { GET } = await import('@/app/api/admin/leads/route')
    const response = await GET(
      new Request('https://demo.example/api/admin/leads', { headers: { cookie } }),
    )
    expect(response.status).toBe(200)
    // The upstream address is chosen here, from the environment - never by the
    // browser, which is what stops this being an open relay.
    expect(seen).toHaveLength(1)
    expect(seen[0].url.startsWith(UPSTREAM)).toBe(true)
    expect(seen[0].auth).toBe(`Bearer ${TOKEN}`)
    // And nothing about either reaches the caller.
    const sent = await everythingSent(response)
    expect(sent).not.toContain(TOKEN)
    expect(sent).not.toContain(UPSTREAM)
  })

  it('answers 401 for an expired session and never calls the upstream', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM

    const called: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL) => {
        called.push(String(input))
        return new Response('{}', { status: 200 })
      }) as typeof fetch,
    )

    const { signAdminSession } = await import('@/lib/admin/session')
    const stale = signAdminSession({ issuedAt: Date.now() - 1000 * 60 * 60 * 24 * 30 })
    const { GET } = await import('@/app/api/admin/leads/route')
    const response = await GET(
      new Request('https://demo.example/api/admin/leads', {
        headers: { cookie: `admin_session=${stale}` },
      }),
    )
    expect(response.status).toBe(401)
    expect(called).toEqual([])
  })

  it('answers 401 for a session signed with the wrong secret', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM
    const { GET } = await import('@/app/api/admin/leads/route')
    const response = await GET(
      new Request('https://demo.example/api/admin/leads', {
        headers: { cookie: 'admin_session=not.a.real.signature' },
      }),
    )
    expect(response.status).toBe(401)
  })

  it('refuses a mutation from another origin even with a valid session', async () => {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM
    const called: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL) => {
        called.push(String(input))
        return new Response('{}', { status: 200 })
      }) as typeof fetch,
    )
    const cookie = await validCookie()
    const { POST } = await import('@/app/api/admin/leads/[id]/decisions/route')
    const response = await POST(
      post(
        'https://demo.example/api/admin/leads/lead-1/decisions',
        { decision: 'qualify', revision: 1 },
        { cookie, origin: 'https://evil.example' },
      ),
      { params: Promise.resolve({ id: 'lead-1' }) },
    )
    // docs/10-: the proxies check origin on mutations. SameSite=Strict is the
    // first defence and this is the one that does not depend on the browser.
    expect(response.status).toBe(403)
    expect(called).toEqual([])
  })
})

describe('the origin check on mutations', () => {
  async function decide(headers: Record<string, string>) {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM
    const forwarded: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL) => {
        forwarded.push(String(input))
        return new Response('{"revision":2}', { status: 201 })
      }) as typeof fetch,
    )
    const { signAdminSession } = await import('@/lib/admin/session')
    const cookie = `admin_session=${signAdminSession({ issuedAt: Date.now() })}`
    const { POST } = await import('@/app/api/admin/leads/[id]/decisions/route')
    const response = await POST(
      new Request('http://internal-host:8060/api/admin/leads/lead-1/decisions', {
        method: 'POST',
        headers: { 'content-type': 'application/json', cookie, ...headers },
        body: JSON.stringify({ decision: 'qualify', revision: 1 }),
      }),
      { params: Promise.resolve({ id: 'lead-1' }) },
    )
    return { status: response.status, forwarded }
  }

  it('accepts a same-origin mutation even when request.url is the internal address', async () => {
    // The case the unit tests originally missed and the container caught: on
    // Railway the URL carries the listen address while Origin and Host carry
    // the public domain, so comparing Origin to request.url refuses every real
    // mutation. This asserts the production shape, not the laptop one.
    const { status, forwarded } = await decide({
      origin: 'https://demo.example',
      host: 'demo.example',
      'x-forwarded-proto': 'https',
    })
    expect(status).toBe(201)
    expect(forwarded).toHaveLength(1)
  })

  it('accepts it when the proxy reports the host in x-forwarded-host', async () => {
    const { status } = await decide({
      origin: 'https://demo.example',
      host: 'internal-host:8060',
      'x-forwarded-host': 'demo.example',
      'x-forwarded-proto': 'https',
    })
    expect(status).toBe(201)
  })

  it('refuses a host that matches over the wrong scheme', async () => {
    const { status, forwarded } = await decide({
      origin: 'http://demo.example',
      host: 'demo.example',
      'x-forwarded-proto': 'https',
    })
    expect(status).toBe(403)
    expect(forwarded).toEqual([])
  })

  it('refuses a mutation with no Origin at all', async () => {
    const { status, forwarded } = await decide({ host: 'demo.example' })
    expect(status).toBe(403)
    expect(forwarded).toEqual([])
  })
})

describe('the upstream token has exactly one reader', () => {
  it('is read in one server module and nowhere else', () => {
    // The same structural shape as the `canPublish: true` guard: a secret with
    // one reader can be reviewed; a secret with three is a search problem.
    const readers = sources()
      .filter((path) =>
        // Comments stripped first: a comment naming the variable is the
        // explanation of this rule, not a breach of it (see
        // `tests/boundaries.test.ts` for the same fix).
        /process\.env\.ADMIN_API_TOKEN/.test(
          readFileSync(path, 'utf-8')
            .replace(/\/\*[\s\S]*?\*\//g, '')
            .replace(/(^|[^:])\/\/.*$/gm, '$1'),
        ),
      )
      .map((path) => path.replace(`${SRC}/`, ''))
    expect(readers).toEqual(['lib/admin/upstream.ts'])
  })

  it('lives in a server-only module that no client component imports', () => {
    // Asserted as existence AND absence, so it cannot pass by there being
    // nothing to find: a negative guard that is vacuously true is not a guard.
    const upstream = sources().filter((path) => path.endsWith('lib/admin/upstream.ts'))
    expect(upstream).toHaveLength(1)
    expect(readFileSync(upstream[0], 'utf-8')).toMatch(/^import 'server-only'/m)

    const offenders = sources()
      .filter((path) => readFileSync(path, 'utf-8').startsWith("'use client'"))
      .filter((path) => /lib\/admin\/upstream/.test(readFileSync(path, 'utf-8')))
    expect(offenders).toEqual([])
  })
})

describe('the cap on pasted text', () => {
  /**
   * god found the paste door open on the API: 8388609 bytes of text returned
   * 201 and were chunked into Postgres. The API is the gate and that is where
   * the fix belongs, but this route forwards the paste, so it refuses first
   * for the same reason the upload route does - a reviewer should not wait for
   * a round trip to be told no.
   */
  async function paste(text: string) {
    process.env.ADMIN_ACCESS_CODE = CODE
    process.env.ADMIN_SESSION_SECRET = SECRET
    process.env.ADMIN_API_TOKEN = TOKEN
    process.env.ADMIN_API_URL = UPSTREAM
    const forwarded: string[] = []
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL) => {
        forwarded.push(String(input))
        return new Response('{"id":"doc-1"}', { status: 201 })
      }) as typeof fetch,
    )
    const { signAdminSession } = await import('@/lib/admin/session')
    const cookie = `admin_session=${signAdminSession({ issuedAt: Date.now() })}`
    const { POST } = await import('@/app/api/admin/knowledge/documents/route')
    const response = await POST(
      post(
        'https://demo.example/api/admin/knowledge/documents',
        { source_type: 'paste', title: 'Payment plans', text },
        { cookie, host: 'demo.example', 'x-forwarded-proto': 'https' },
      ),
    )
    return { status: response.status, forwarded }
  }

  it('refuses an over-cap paste and forwards nothing', async () => {
    const { MAX_UPLOAD_BYTES } = await import('@/lib/admin/knowledge')
    const { status, forwarded } = await paste('x'.repeat(MAX_UPLOAD_BYTES + 1))
    expect(status).toBe(413)
    expect(forwarded).toEqual([])
  })

  it('measures the encoded bytes, not the character count', async () => {
    const { MAX_UPLOAD_BYTES } = await import('@/lib/admin/knowledge')
    // Two bytes per character in UTF-8, so half the cap in characters is the
    // whole cap in bytes. Counting characters would admit this.
    const { status, forwarded } = await paste('م'.repeat(MAX_UPLOAD_BYTES / 2 + 1))
    expect(status).toBe(413)
    expect(forwarded).toEqual([])
  })

  it('forwards a paste that fits', async () => {
    const { status, forwarded } = await paste('Two bedrooms start at AED 2,000,000.')
    expect(status).toBe(201)
    expect(forwarded).toHaveLength(1)
  })

  it('uses the byte cap that data/knowledge.yaml states', async () => {
    // The API reads that file; this constant is a copy of the number, and a
    // copy with no test is a number that drifts on somebody else's edit.
    const yaml = readFileSync(join(process.cwd(), '..', 'data', 'knowledge.yaml'), 'utf-8')
    const stated = /^max_source_bytes:\s*(\d+)/m.exec(yaml)
    expect(stated, 'data/knowledge.yaml must state max_source_bytes').not.toBeNull()
    const { MAX_UPLOAD_BYTES } = await import('@/lib/admin/knowledge')
    expect(MAX_UPLOAD_BYTES).toBe(Number(stated?.[1]))
  })
})
