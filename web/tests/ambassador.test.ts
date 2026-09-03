// @vitest-environment node
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The ambassador's name, and what happens when a language has not got one.
 *
 * The reader looks for `../data/ambassadors.yaml` relative to the working
 * directory, the same way the inventory reader does, so these tests move the
 * working directory rather than mocking `fs`: what is under test is the whole
 * read, including the path.
 */

let cwd: string

async function withFile(contents: string | null) {
  const root = await mkdtemp(join(tmpdir(), 'ambassadors-'))
  await mkdir(join(root, 'data'), { recursive: true })
  await mkdir(join(root, 'web'), { recursive: true })
  if (contents !== null) {
    await writeFile(join(root, 'data', 'ambassadors.yaml'), contents, 'utf-8')
  }
  vi.spyOn(process, 'cwd').mockReturnValue(join(root, 'web'))
}

beforeEach(() => {
  cwd = process.cwd()
  vi.resetModules()
})

afterEach(() => {
  vi.restoreAllMocks()
  process.chdir(cwd)
})

describe('the ambassador name', () => {
  it('reads the authored name', async () => {
    await withFile('en: Jane\nar: ""\nhi: ""\n')
    const { loadAmbassadorNames } = await import('@/lib/ambassador')
    const names = await loadAmbassadorNames()
    expect(names.en).toBe('Jane')
  })

  it('falls back for a language nobody has named yet, rather than showing a blank', async () => {
    await withFile('en: Jane\nar: ""\nhi: ""\n')
    const { loadAmbassadorNames } = await import('@/lib/ambassador')
    const { AMBASSADOR_FALLBACK } = await import('@/lib/ambassador.shared')
    const names = await loadAmbassadorNames()
    // An empty value is the "not authored by somebody who speaks it" marker,
    // the same convention the other per-language tables use.
    expect(names.ar).toBe(AMBASSADOR_FALLBACK)
    expect(names.hi).toBe(AMBASSADOR_FALLBACK)
  })

  it('opens the page even with no file at all', async () => {
    await withFile(null)
    const { loadAmbassadorNames } = await import('@/lib/ambassador')
    const { AMBASSADOR_FALLBACK } = await import('@/lib/ambassador.shared')
    const names = await loadAmbassadorNames()
    // A missing file must not be a 500 on the page a client was sent to.
    expect(names).toEqual({
      en: AMBASSADOR_FALLBACK,
      ar: AMBASSADOR_FALLBACK,
      hi: AMBASSADOR_FALLBACK,
    })
  })

  it('handles the quoting and spacing a hand-edited file will actually have', async () => {
    await withFile("en:   Jane  \nar: 'سارة'\nhi: \"आशा\"\n")
    const { loadAmbassadorNames } = await import('@/lib/ambassador')
    const names = await loadAmbassadorNames()
    expect(names.en).toBe('Jane')
    expect(names.ar).toBe('سارة')
    expect(names.hi).toBe('आशा')
  })

  it('ships a file the shipped page can actually read', async () => {
    // No mocking: the real file, read from the real repo, because a schema
    // agreed with another agent is worth nothing if the file does not match it.
    const { loadAmbassadorNames } = await import('@/lib/ambassador')
    const names = await loadAmbassadorNames()
    expect(names.en).toBe('Jane')
  })
})
