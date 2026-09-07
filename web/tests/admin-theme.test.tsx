import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement, ReactNode } from 'react'

/**
 * ONE theme for the whole admin, door included (task-web-admin-premium-pass,
 * finding G1).
 *
 * THE DEFECT THESE CASES DESCRIBE. `/admin` shipped as two products. The
 * dashboard mounted Astryx's `neutralTheme` at its default `mode="system"`, so
 * AppShell painted its own light surface; the sign-in door mounted the same
 * provider but painted nothing, so the root `globals.css` ink-950 body showed
 * through behind it. A visitor signed in on a dark screen and landed on a white
 * one, and which of the two the dashboard chose depended on the visitor's
 * operating system rather than on anything this repository decides.
 *
 * WHAT IS ASSERTED, and why each is here rather than left to a screenshot:
 *
 *   the theme is OURS and it is NAMED. `Theme` reflects `theme.name` onto
 *   `data-astryx-theme`, which is the attribute its own @scope'd CSS keys off,
 *   so the name is not decoration: it is the selector every themed rule hangs
 *   from. A surface outside it is unstyled, and a surface inside the WRONG one
 *   is styled by somebody else's palette. Both look like "the CSS did not
 *   load" and neither is visible in a unit test that only queries roles.
 *
 *   the mode is PINNED. `mode="system"` is not a design decision, it is the
 *   absence of one: it hands the product's identity to `prefers-color-scheme`,
 *   which means the contrast ratios measured for this change describe whichever
 *   half of the visitors match the machine they were measured on. Pinning it is
 *   what makes a measured ratio a fact about the product.
 *
 *   the font families are NOT NAMED IN THE THEME. This is the trap the Astryx
 *   theme template warns about in as many words - a named family with no file
 *   loads nothing, warns about nothing, and the fallback silently becomes your
 *   theme. next/font self-hosts the faces and generates its own family names at
 *   build time, so the theme cannot spell them; it has to reference the CSS
 *   variables next/font defines. Get that wrong and every page renders in
 *   `system-ui` with no error anywhere - the single most silent failure in this
 *   whole change, and the reason it is pinned at the token level where it can
 *   be read rather than in a screenshot where it cannot.
 *
 *   the accent is a light/dark SEED PAIR, not a token override. Measured on a
 *   throwaway probe: overriding `--color-accent` re-points the muted, text and
 *   icon references but leaves `--color-on-accent` generated from the seed, so
 *   a brass fill keeps the old foreground and the pair fails contrast while
 *   every individual token looks correct.
 */

/**
 * Imported with a variable specifier and `@vite-ignore`, the house pattern: a
 * literal dynamic import resolves at TRANSFORM time, so a module that does not
 * exist yet fails the FILE to load, vitest reports "no tests", and a RED commit
 * has nothing to count. Deferring it to runtime makes each missing module one
 * failing CASE.
 */
async function load(specifier: string): Promise<Record<string, never>> {
  return (await import(/* @vite-ignore */ specifier)) as Record<string, never>
}

let pathname = '/admin'

vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
}))

/**
 * The route group's real provider composition.
 *
 * `commitSha` is passed explicitly - the admin LAYOUT reads it from the
 * environment on the server and hands it down, and these cases are about the
 * theme rather than the footer, so null is the honest fixture: it is what
 * every environment except Railway supplies.
 */
async function providers(): Promise<
  (p: { commitSha: string | null; children: ReactNode }) => ReactElement
> {
  const { AdminProviders } = (await load('@/app/admin/providers')) as unknown as {
    AdminProviders: (p: {
      commitSha: string | null
      children: ReactNode
    }) => ReactElement
  }
  return AdminProviders
}

/** The theme wrapper `Theme` renders, found from something inside it. */
function themeWrapperAround(element: HTMLElement): HTMLElement {
  const wrapper = element.closest('[data-astryx-theme]')
  expect(wrapper).not.toBeNull()
  return wrapper as HTMLElement
}

beforeEach(() => {
  pathname = '/admin'
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the admin theme', () => {
  it('wraps the DOOR in the Binghatti theme, not in the default neutral one', async () => {
    const AdminProviders = await providers()
    const { AdminShell } = (await load('@/components/admin-shell')) as unknown as {
      AdminShell: (p: { configured: boolean }) => ReactElement
    }
    render(
      <AdminProviders commitSha={null}>
        <AdminShell configured />
      </AdminProviders>,
    )
    // Found from the door's own heading, so this cannot pass on a wrapper that
    // renders nothing inside it.
    const heading = screen.getByRole('heading', { level: 1 })
    expect(themeWrapperAround(heading)).toHaveAttribute('data-astryx-theme', 'binghatti')
  })

  it('wraps the DASHBOARD in that same theme, so the two surfaces are one product', async () => {
    const AdminProviders = await providers()
    const { AdminAppShell } = (await load('@/components/admin/app-shell')) as unknown as {
      AdminAppShell: (p: { title: string; children: ReactElement }) => ReactElement
    }
    render(
      <AdminProviders commitSha={null}>
        <AdminAppShell title="Overview">
          <p>Page body</p>
        </AdminAppShell>
      </AdminProviders>,
    )
    expect(themeWrapperAround(screen.getByRole('main'))).toHaveAttribute(
      'data-astryx-theme',
      'binghatti',
    )
  })

  it('pins the mode instead of following the visitor operating system', async () => {
    /*
     * `Theme` omits `data-theme` entirely at its default `mode="system"` and
     * lets `prefers-color-scheme` decide - which is how a WCAG ratio measured
     * on one machine stops describing the product. Asserting the attribute is
     * present AND dark is the same assertion as "this palette is the one that
     * was measured".
     */
    const AdminProviders = await providers()
    const { AdminShell } = (await load('@/components/admin-shell')) as unknown as {
      AdminShell: (p: { configured: boolean }) => ReactElement
    }
    render(
      <AdminProviders commitSha={null}>
        <AdminShell configured />
      </AdminProviders>,
    )
    const wrapper = themeWrapperAround(screen.getByRole('heading', { level: 1 }))
    expect(wrapper).toHaveAttribute('data-theme', 'dark')
  })

  it('leads the door with the brand rather than the word Admin', async () => {
    /*
     * Finding G8: the door's h1 was the letterspaced label "Admin", which
     * names the tool and not the company - the one screen a visitor meets
     * before anything else identified nothing about whose product it is.
     */
    const { AdminShell } = (await load('@/components/admin-shell')) as unknown as {
      AdminShell: (p: { configured: boolean }) => ReactElement
    }
    render(<AdminShell configured />)
    // The positive claim first: there IS a level-one heading and it names the
    // brand. The negative below only means something once that holds.
    expect(screen.getByRole('heading', { level: 1, name: /binghatti/i })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^admin$/i })).toBeNull()
  })
})

describe('the Binghatti theme tokens', () => {
  async function theme(): Promise<{ name: string; tokens: Record<string, string> }> {
    const mod = (await load('@/theme/binghatti')) as unknown as {
      binghattiTheme: { name: string; tokens: Record<string, string> }
    }
    return mod.binghattiTheme
  }

  it('is one built theme carrying its own name', async () => {
    const built = await theme()
    expect(built.name).toBe('binghatti')
    // `tokens` is what `Theme` turns into custom properties. An empty map is a
    // theme that changes nothing, which is the shape a mis-wired build leaves
    // behind, so the cases below have something to stand on.
    expect(Object.keys(built.tokens).length).toBeGreaterThan(0)
  })

  it('references the font variables next/font defines instead of naming a family', async () => {
    /*
     * THE SILENT ONE. Astryx's own theme template: a named family with no file
     * loads nothing and warns about nothing, so the fallback becomes the
     * theme. next/font generates its family names at build time, so the theme
     * cannot spell them - it has to point at the variables next/font declares.
     * Both halves are asserted from the SAME constant the font setup uses, so
     * renaming a variable in one place fails here rather than shipping a
     * dashboard rendered in system-ui.
     */
    const { FONT_VARIABLES } = (await load('@/theme/fonts')) as unknown as {
      FONT_VARIABLES: { display: string; body: string }
    }
    const built = await theme()
    const body = built.tokens['--font-family-body']
    const heading = built.tokens['--font-family-heading']
    expect(body).toBeTypeOf('string')
    expect(heading).toBeTypeOf('string')
    expect(body).toContain(`var(${FONT_VARIABLES.body})`)
    expect(heading).toContain(`var(${FONT_VARIABLES.display})`)
    // And a fallback behind each, because a var() that resolves to nothing
    // leaves the whole declaration invalid rather than falling back.
    expect(body).toMatch(/,\s*\S/)
    expect(heading).toMatch(/,\s*\S/)
  })

  it('declares those same variables in the next/font call, which cannot import them', async () => {
    /*
     * ADDED WITH THE GREEN, not with the RED, and the reason is the finding
     * itself: the case above was written expecting `faces.ts` to IMPORT
     * `FONT_VARIABLES`, which would have made the compiler hold the two sides
     * together. next/font forbids it - it is a compile-time transform, not a
     * function call, and `variable: FONT_VARIABLES.display` fails the build
     * with "Font loader values must be explicitly written literals". So the
     * literals have to be written twice, and nothing in the type system
     * connects them any more.
     *
     * Reading the source as TEXT is a blunt instrument and it is the right one
     * here. The failure it guards is silent in every direction: rename the
     * variable in `fonts.ts` and the theme follows it while next/font keeps
     * declaring the old name, so `--font-family-body` resolves to nothing,
     * the declaration is dropped, and the entire admin renders in the fallback
     * stack with no error, no warning and no visual clue beyond "the type
     * looks a bit plain". A test that cannot be fooled by a rename is worth
     * more than an elegant one that never runs.
     */
    const { FONT_VARIABLES } = (await load('@/theme/fonts')) as unknown as {
      FONT_VARIABLES: { display: string; body: string }
    }
    // Resolved from the project root rather than from `import.meta.url`:
    // under vitest's jsdom environment that is not a `file:` URL, and
    // `readFile` rejects with "The URL must be of scheme file". vitest runs
    // with `web/` as the cwd (see the `include` glob in vitest.config.ts),
    // and a wrong path here throws rather than passing quietly.
    const source = await readFile(resolve(process.cwd(), 'src/theme/faces.ts'), 'utf8')
    // The positive precondition: this really is the module that calls
    // next/font. Without it, a renamed or deleted file would leave the two
    // assertions below passing over an empty string.
    expect(source).toContain('next/font/local')
    for (const variable of [FONT_VARIABLES.display, FONT_VARIABLES.body]) {
      expect(source, `faces.ts must declare variable: '${variable}'`).toContain(
        `variable: '${variable}'`,
      )
    }
  })

  it('seeds the brass accent as a light/dark pair so its foreground is generated with it', async () => {
    /*
     * Measured, not preferred: an override of `--color-accent` re-points the
     * muted/text/icon references and leaves `--color-on-accent` generated from
     * the ORIGINAL seed, so a brass fill keeps the old foreground. A seed pair
     * regenerates the whole ramp, which is why the accent is expressed as one.
     */
    const built = await theme()
    const accent = built.tokens['--color-accent']
    expect(accent).toBeTypeOf('string')
    expect(accent).toMatch(/^light-dark\(/)
    expect(built.tokens['--color-on-accent']).toBeTypeOf('string')
  })

  it('tightens the radius ramp to the hairline end rather than the default pills', async () => {
    /*
     * Finding G1 names 12px rounded cards; the direction is 2-4px. Radius is
     * base x multiplier on a NON-LINEAR ramp - 2/1 and 4/0.5 both give inner
     * 2, element 4, container 6, page 14 - so the two outer steps do not come
     * down with the base and need naming explicitly. Asserted in pixels
     * because that is the thing the finding is about.
     */
    const built = await theme()
    for (const token of [
      '--radius-inner',
      '--radius-element',
      '--radius-container',
      '--radius-page',
    ]) {
      const value = built.tokens[token]
      expect(value, token).toBeTypeOf('string')
      expect(Number.parseFloat(value), token).toBeLessThanOrEqual(4)
    }
  })
})
