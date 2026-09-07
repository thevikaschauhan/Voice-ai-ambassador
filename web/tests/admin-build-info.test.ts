import { afterEach, beforeEach, describe, expect, it } from 'vitest'

/**
 * Which commit the web service was built from (finding G2's footer).
 *
 * Railway sets `RAILWAY_GIT_COMMIT_SHA` on every build and deployment, so
 * there is nothing to mint and no build argument to plumb: the admin layout is
 * a server component and reads the live process environment at request time.
 *
 * BOTH BRANCHES ARE PINNED HERE because the absent one is the one that ships
 * everywhere except Railway - locally, in CI, in every test - and the wrong
 * answer for it is a plausible-looking string. "dev", "unknown" or a blank
 * space on a deployed page all tell a reviewer something FALSE, where no line
 * at all tells them only that the page is not saying. A fallback is the defect
 * these cases exist to prevent, which is why one of them asserts a null rather
 * than a value.
 */

const VARIABLE = 'RAILWAY_GIT_COMMIT_SHA'

// Captured and restored around each case: process.env is shared by the whole
// worker, so a leaked value would make a later case pass for the wrong reason.
let original: string | undefined

beforeEach(() => {
  original = process.env[VARIABLE]
  delete process.env[VARIABLE]
})

afterEach(() => {
  if (original === undefined) delete process.env[VARIABLE]
  else process.env[VARIABLE] = original
})

async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

async function buildInfo(): Promise<{
  commitSha: () => string | null
  shortSha: (sha: string) => string
}> {
  return (await load('@/lib/admin/build-info')) as unknown as {
    commitSha: () => string | null
    shortSha: (sha: string) => string
  }
}

describe('the build identity', () => {
  it('reports the sha the platform set', async () => {
    process.env[VARIABLE] = '3bf4ec63d9a1f0e2b7c4a5968d3e1f2a0b9c8d7e'
    const { commitSha } = await buildInfo()
    expect(commitSha()).toBe('3bf4ec63d9a1f0e2b7c4a5968d3e1f2a0b9c8d7e')
  })

  it('reports nothing at all when the variable is unset', async () => {
    const { commitSha } = await buildInfo()
    expect(commitSha()).toBeNull()
  })

  it('treats a blank variable as unset rather than rendering an empty line', async () => {
    // The platform sets this, but a hand-edited service variable can be blank
    // or whitespace, and `''` would otherwise render an element with nothing
    // in it - a footer line that looks broken rather than absent.
    for (const blank of ['', '   ', '\t']) {
      process.env[VARIABLE] = blank
      const { commitSha } = await buildInfo()
      expect(commitSha(), JSON.stringify(blank)).toBeNull()
    }
  })

  it('shortens to the seven characters a human reads', async () => {
    const { shortSha } = await buildInfo()
    expect(shortSha('3bf4ec63d9a1f0e2b7c4a5968d3e1f2a0b9c8d7e')).toBe('3bf4ec6')
    // A sha shorter than seven is returned whole rather than padded: it is not
    // this function's job to decide that the platform lied about the format.
    expect(shortSha('abc')).toBe('abc')
  })
})
