// @vitest-environment node
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Structural claims about where things may be imported, asserted over the
 * source rather than remembered.
 *
 * Each of these is a property a reviewer would otherwise have to re-derive by
 * reading every file, and each one fails LOUDLY the first time somebody adds an
 * import in the wrong place - which is the only kind of guard that survives.
 */

const SRC = join(process.cwd(), 'src')

function sources(dir = SRC): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(entry) ? [path] : []
  })
}

function read(path: string): string {
  return readFileSync(path, 'utf-8')
}

describe('import boundaries', () => {
  it('keeps livekit-client on the client, where a browser API can actually exist', () => {
    const offenders = sources()
      .filter((path) => /from 'livekit-client'/.test(read(path)))
      .filter((path) => !read(path).startsWith("'use client'"))
    // livekit-client reaches for MediaStream, AudioContext and Audio. Importing
    // it into a server component does not fail at build time, it fails on the
    // first request that touches it.
    expect(offenders).toEqual([])
  })

  it('keeps the signing secret and the access code on the server', () => {
    const offenders = sources()
      .filter((path) => read(path).startsWith("'use client'"))
      .filter((path) => /process\.env\.(LIVEKIT_API_SECRET|DEMO_ACCESS_CODE)/.test(read(path)))
    expect(offenders).toEqual([])
  })

  it('reads no NEXT_PUBLIC_ variable anywhere', () => {
    // Not a style rule. A NEXT_PUBLIC_ value is compiled into the client
    // bundle, so an access code there is decoration and a key there is a leak
    // (docs/09-). The absence is the security property, so it is asserted.
    //
    // Matched on `process.env.NEXT_PUBLIC_` rather than on the bare prefix,
    // because that is the only form Next inlines - and because the looser
    // pattern flagged the comment in `hosted.ts` that explains this rule,
    // which is a test asserting that nobody may write the word.
    const offenders = sources().filter((path) =>
      /process\.env\.NEXT_PUBLIC_/.test(read(path)),
    )
    expect(offenders).toEqual([])
  })

  it('mints a publishing token in exactly one place', () => {
    const publishers = sources().filter((path) => /canPublish:\s*true/.test(read(path)))
    expect(publishers.map((path) => path.replace(`${SRC}/`, ''))).toEqual(['lib/livekit/talk.ts'])
  })

  it('keeps the viewer grant listen-only', () => {
    // The pair of grants is the design (docs/09-): if an edit ever loosens the
    // viewer grant into a publishing one, this fails rather than a reviewer
    // having to notice.
    const viewer = read(join(SRC, 'lib/livekit/room.ts'))
    expect(viewer).toMatch(/canPublish:\s*false/)
    expect(viewer).toMatch(/canPublishData:\s*false/)
    expect(viewer).toMatch(/hidden:\s*true/)
  })
})
