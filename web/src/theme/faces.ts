import localFont from 'next/font/local'

/**
 * The two faces, self-hosted from the lockfile.
 *
 * WHY next/font/local AND NOT next/font/google, which was the first attempt:
 * `next/font/google` downloads each face from fonts.googleapis.com during
 * `next build`. That is still self-hosting at runtime - the file is served
 * from our own origin and no visitor talks to Google - but it makes the BUILD
 * depend on Google being reachable, and it failed exactly that way here
 * ("Failed to fetch Cormorant Garamond from Google Fonts"). A build that needs
 * a third party up is not a build. The faces are OFL, so they are pinned as
 * npm dependencies instead (`@fontsource/*` at an exact version, in
 * package-lock.json) and read off disk. Same self-hosting, same next/font
 * metric handling, no network and nothing to be offline for.
 *
 * EVERY VALUE HERE IS A WRITTEN LITERAL, and it has to be. next/font is a
 * compile-time transform, not a function call: passing `variable:
 * FONT_VARIABLES.display` fails the build with "Font loader values must be
 * explicitly written literals". So the shared constant in `./fonts.ts` cannot
 * feed this file, and the two are held together by a case in
 * `admin-theme.test.tsx` that reads this source and compares the literals
 * against it. A source-text assertion is a blunt instrument; it is the right
 * one here, because the framework forbids the indirection that would have made
 * the compiler do this job, and the failure it guards is silent - a variable
 * name that does not match renders the whole admin in the fallback stack with
 * no error anywhere.
 *
 * `adjustFontFallback` is left at its default (on): next/font measures the
 * face and synthesises a size-adjusted local fallback, so the swap when the
 * real file lands does not reflow the page.
 */

/** The serif display face: the wordmark, page titles, count numerals. */
export const displayFace = localFont({
  src: [
    {
      path: '../../node_modules/@fontsource/cormorant-garamond/files/cormorant-garamond-latin-500-normal.woff2',
      weight: '500',
      style: 'normal',
    },
    {
      path: '../../node_modules/@fontsource/cormorant-garamond/files/cormorant-garamond-latin-600-normal.woff2',
      weight: '600',
      style: 'normal',
    },
  ],
  variable: '--font-admin-display',
  display: 'swap',
  fallback: ['ui-serif', 'Georgia', 'Times New Roman', 'serif'],
})

/** The sans body face: everything else. */
export const bodyFace = localFont({
  src: [
    {
      path: '../../node_modules/@fontsource/manrope/files/manrope-latin-400-normal.woff2',
      weight: '400',
      style: 'normal',
    },
    {
      path: '../../node_modules/@fontsource/manrope/files/manrope-latin-500-normal.woff2',
      weight: '500',
      style: 'normal',
    },
    {
      path: '../../node_modules/@fontsource/manrope/files/manrope-latin-600-normal.woff2',
      weight: '600',
      style: 'normal',
    },
  ],
  variable: '--font-admin-body',
  display: 'swap',
  fallback: ['ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
})
