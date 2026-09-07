import { defineTheme } from '@astryxdesign/core/theme'
import { neutralTheme } from '@astryxdesign/theme-neutral'

import { FONT_VARIABLES } from './fonts'

/**
 * The admin's one theme, for the door and the dashboard alike (finding G1).
 *
 * THIS IS THE ONLY FILE THAT DECIDES HOW /admin LOOKS. That is deliberate and
 * it is the point: a direction change is an edit here plus `astryx theme
 * build`, not a sweep through components. Light instead of dark is `mode` in
 * `app/admin/providers.tsx` plus the surface block below; dropping the serif
 * is one line of `typography`. `binghatti.css` / `binghatti.js` next to this
 * file are GENERATED - never edit them, and CI's `astryx theme build --check`
 * fails if they stop matching this source.
 *
 * WHAT IS GENERATED AND WHAT IS OURS, because the difference is a contrast
 * guarantee. Astryx's HCT generator holds generated text at >= 4.5:1 against
 * its surface and `--color-border-emphasized` at >= 3:1 - but only for what it
 * generates. Its own template is explicit that hand-writing one side of a pair
 * transfers that pair's contrast to you, and that the status hues (success,
 * warning, error) and categorical hues are NOT derived from the accent: they
 * keep defaults tuned for the DEFAULT surfaces.
 *
 * So the surfaces here are GENERATED, from `neutralStyle: 'warm'`, rather than
 * hand-written to the demo's ink hexes. Pasting those hexes in would have read
 * as more faithful to the brand and would have quietly moved every text pair in
 * the system out from under the guarantee - hundreds of pairs, ours to prove,
 * to save a few points of hue. The badge system is the one place we do own the
 * numbers, which is where PR E's measurements go and why they are in the PR
 * body rather than assumed.
 *
 * The demo's `@theme` tokens in `globals.css` are NOT reachable from here, and
 * that file says so in as many words. `/` and `/admin` share a brand, not a
 * stylesheet: the seed below is the brand's brass by value, and the ramp
 * around it is Astryx's.
 */
export const binghattiTheme = defineTheme({
  // Becomes `data-astryx-theme="binghatti"`, which is the selector every
  // themed rule below is scoped to - and what `admin-theme.test.tsx` asserts
  // both surfaces are inside. The generated files are named from THIS, not
  // from the filename.
  name: 'binghatti',

  // A complete base to override, so this file lists only what differs and the
  // built output stays self-contained.
  extends: neutralTheme,

  /**
   * Brass, seeded rather than overridden.
   *
   * MEASURED on a throwaway probe: setting `--color-accent` in `tokens`
   * re-points the muted, text and icon references but leaves
   * `--color-on-accent` generated from the ORIGINAL seed, so a brass fill
   * keeps a foreground toned for blue. Every token looks right in isolation
   * and the pair fails. A seed regenerates the whole ramp including its own
   * on-colour, so the accent is expressed here and not down in `tokens`.
   *
   * The pair is the brand's brass-600 and brass-400 (globals.css, by value
   * only). The generator re-tones each to hold contrast on its own scheme's
   * surfaces, so the compiled values are not these two hexes - which is the
   * behaviour we want and the reason the compiled pair is quoted in the PR
   * body rather than these.
   */
  color: { accent: ['#8f7139', '#d3b271'], neutralStyle: 'warm', contrast: 'standard' },

  /**
   * A serif display face and a sans body, both by VARIABLE.
   *
   * next/font self-hosts each face at build and generates its family name, so
   * neither can be spelled here; `layout.tsx` declares these two variables and
   * this points at them. The fallbacks matter more than usual: a `var()` that
   * resolves to nothing invalidates the whole declaration rather than falling
   * back, so each keeps a real stack behind it. See `./fonts.ts` for why the
   * names live in one place - the failure mode is silent.
   */
  typography: {
    scale: { base: 15, ratio: 1.2 },
    body: {
      family: `var(${FONT_VARIABLES.body})`,
      fallbacks: 'ui-sans-serif, system-ui, -apple-system, sans-serif',
    },
    // Serif, and lighter than the default: at display sizes a high-contrast
    // serif carries weight from its own letterforms, and `bold` on top of that
    // reads as shouting rather than as luxury. Restraint is the direction.
    heading: {
      family: `var(${FONT_VARIABLES.display})`,
      fallbacks: 'ui-serif, Georgia, "Times New Roman", serif',
      weight: 'medium',
      weights: { 1: 'medium', 2: 'medium', 3: 'medium' },
    },
  },

  /**
   * Hairline corners: 2px inner, 4px element.
   *
   * MEASURED, because the ramp is NOT linear in the base - base 2 with
   * multiplier 1 and base 4 with multiplier 0.5 both produce inner 2,
   * element 4, container 6, page 14. So the base brings the two inner steps
   * where the direction wants them and leaves the two outer steps above it;
   * `container` and `page` are pinned in `tokens` below rather than chased by
   * tuning the multiplier, which would have moved all four at once.
   */
  radius: { base: 2, multiplier: 1 },

  tokens: {
    // The two steps `radius.base` above cannot reach. Finding G1 names 12px
    // rounded cards as part of what reads generic; a card and the page it sits
    // on are the two largest radii on screen, so leaving them at 6 and 14
    // would have kept the look this change is about.
    '--radius-container': '4px',
    '--radius-page': '4px',

    // Keyboard focus is brass everywhere, which is one of the three things the
    // brand lets the accent mark. `var()` rather than a hex so the seed above
    // stays the single source for the colour.
    '--focus-outline-color': 'var(--color-accent)',

    // No glow and no lift: the direction is hairline borders doing the work of
    // separation. Astryx's defaults are soft shadows on cards and popovers,
    // which is the generic-dashboard look in one property. `--shadow-med` and
    // `--shadow-high` keep a real value because a popover and a drawer float
    // ABOVE the page and need to read that way - restraint is not the same as
    // flattening a layer that is genuinely on top.
    '--shadow-low': 'none',
  },

  components: {
    /**
     * THE PRIMARY ACTION IS A BRASS OUTLINE, NOT A BRASS FILL.
     *
     * FOUND IN THE BROWSER, not by any case above: on the door, Astryx's
     * `variant="primary"` renders a full-width solid `--color-accent` bar, and
     * "Sign in" came out as the largest block of brass on the screen. The
     * direction for this pass allows brass on exactly four things - an active
     * nav marker, a focus ring, a primary action's border and text, and a
     * "needs action" state - and says never a large fill. A 330px solid brass
     * bar is the one thing it rules out, and I had shipped it as the most
     * prominent element on the first screen anybody sees.
     *
     * Fixed HERE rather than at the call site, and that is the whole argument
     * for a theme: there are primary buttons on the door, the knowledge
     * intake, the figure reviews and the decision form, and PRs E and F add
     * more. A per-call-site fix would be five edits now, an unbounded number
     * later, and a rule nobody can enforce. One override moves every one of
     * them and keeps the direction checkable in a single file.
     *
     * Contrast is measured, not assumed: brass #EAC16C on the card surface
     * #201B12 is 10.06:1, well past AA for text, and the 1px border carries
     * the same colour so the control's EDGE is as legible as its label - which
     * matters more here than usual, because the fill is what used to define
     * the hit area.
     */
    /**
     * THE ACTIVE NAV ITEM IS A BRASS RULE, NOT A PILL.
     *
     * FOUND IN THE BROWSER, and it is the half of finding G2 my first pass did
     * not actually close. G2 says the sidebar has "no active marker beyond a
     * grey pill"; I themed everything around it and left the pill in place -
     * measured as `rgba(233,225,213,0.2)`, which is text-primary at 20%, so
     * the current section was marked by a translucent white lozenge on a warm
     * ink sidebar. Warmer than before and still a pill, still not brass, and
     * still the thing the finding names.
     *
     * A 3px inset rule on the leading edge plus a brass label is a MARKER: it
     * says which item is current without becoming a filled shape, which is the
     * direction's one rule about this colour. The fill is removed rather than
     * tinted, because a brass fill here would be the largest brass area on
     * every page - exactly what the primary button override below exists to
     * prevent.
     *
     * `inset 3px 0 0 0` rather than a border: a border changes the item's box
     * and shifts every label 3px right when it becomes current, which reads as
     * the list twitching as you navigate. Contrast measured: brass #EAC16C on
     * the sidebar's #171001 is 11.11:1.
     */
    'side-nav-item': {
      'selected:selected': {
        backgroundColor: 'transparent',
        color: 'var(--color-accent)',
        boxShadow: 'inset 3px 0 0 0 var(--color-accent)',
      },
    },

    button: {
      'variant:primary': {
        backgroundColor: 'transparent',
        borderWidth: 'var(--border-width)',
        borderStyle: 'solid',
        borderColor: 'var(--color-accent)',
        color: 'var(--color-accent)',
        // A faint wash on hover, so the control still answers the pointer
        // without becoming the fill this override exists to remove.
        ':hover': { backgroundColor: 'var(--color-accent-muted)' },
      },
    },
  },
})
