/**
 * The two CSS custom properties the admin's faces arrive on.
 *
 * WHY THIS FILE EXISTS AT ALL, and it is one line of substance for a reason.
 *
 * next/font self-hosts each face at build time and GENERATES its family name
 * (`__Cormorant_Garamond_a1b2c3`), so no file in this repository can spell it.
 * The theme therefore cannot name a family; it has to point at a variable, and
 * next/font has to declare that same variable. Two places, one string, and no
 * compiler between them.
 *
 * The failure that makes this worth a module: Astryx's own theme template
 * warns that a named family with no file loaded "loads nothing and warns about
 * nothing - the fallback silently becomes your theme". A typo in one of these
 * names does not error, does not warn and does not show up in a diff review.
 * It renders the entire admin in system-ui, which looks like a styling opinion
 * rather than a bug. Both sides read from here, and `admin-theme.test.tsx`
 * asserts the theme's tokens against these values, so a rename fails a case.
 */
export const FONT_VARIABLES = {
  /** The serif display face: the wordmark, page titles, count numerals. */
  display: '--font-admin-display',
  /** The sans body face: everything else. */
  body: '--font-admin-body',
} as const
