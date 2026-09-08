'use client'

import { ProgressBar } from '@astryxdesign/core/ProgressBar'
import { Text } from '@astryxdesign/core/Text'
import { SIGNAL_LABELS } from '@/lib/admin/leads'
import type { ScoreItem } from '@/lib/admin/leads'

/**
 * How an interest score reads on screen, on every surface that shows one.
 *
 * ONE MODULE BECAUSE TWO SURFACES SHOW THE SAME NUMBER: the lead list's Score
 * column and the lead detail's breakdown and total. The status vocabularies
 * are kept in one place for exactly this reason (`status-badge.tsx`), and a
 * band is the same kind of thing - two colour vocabularies for one number is
 * how a reviewer learns to distrust the colour.
 *
 * EVERY FIGURE IS OUT OF 100 (human request, 2026-09-08: "the score should be
 * out of 100 for every category and make them color coded"). The rubric
 * weights its signals differently - 15, 15, 10, 20, 25, 10 and 5 points - so a
 * row used to read "15 of 15" beside "0 of 10" and comparing two of them meant
 * dividing first. Normalising each to 0-100 is what makes the column scannable.
 *
 * THE COST OF THAT, SAID OUT LOUD: normalised categories no longer add up to
 * the total, because the total is the weighted sum of POINTS and these are
 * percentages of each signal's own weight. The rubric's own total is already
 * 0-100 (`data/interest-score.yaml`), so the big figure and the rows are on the
 * same scale but not in the same arithmetic. The weight is no longer on screen;
 * it is in docs/10-'s rubric table.
 *
 * THE COLOUR IS REDUNDANT, NEVER LOAD-BEARING. docs/10-: "a badge distinguished
 * only by its variant is a badge a colour-blind reviewer cannot read". Every
 * band here sits on a figure that already states the value, and each category
 * bar carries `aria-valuenow`, so nothing is encoded in hue alone.
 */

export type ScoreBand = 'low' | 'medium' | 'high'

/** The bands the card fixes: 0-39 red, 40-69 amber, 70-100 green. */
const LOW_CEILING = 39
const MEDIUM_CEILING = 69

export function scoreBand(value: number): ScoreBand {
  if (value <= LOW_CEILING) return 'low'
  if (value <= MEDIUM_CEILING) return 'medium'
  return 'high'
}

/**
 * A signal's award as a 0-100 figure.
 *
 * A signal that was not observed is 0 rather than absent, because a breakdown
 * that silently drops its zeros reads as a shorter rubric than the one that
 * ran. `max_points <= 0` is guarded rather than trusted: a rubric revision that
 * ever shipped a zero-weight signal would otherwise render NaN across the page.
 */
export function categoryScore(item: ScoreItem): number {
  if (!item.observed || item.max_points <= 0) return 0
  return Math.round((item.points_awarded / item.max_points) * 100)
}

/**
 * Astryx's own status variants, so the colours come from the theme rather than
 * from hexes written into a component. `astryx theme build` keeps them in step
 * with `binghatti.theme.ts`.
 */
const BAND_VARIANT = {
  low: 'error',
  medium: 'warning',
  high: 'success',
} as const

/**
 * The same three tokens as text. Written out per band rather than composed,
 * because Tailwind scans for whole class names and a template-built one is a
 * class that silently never ships.
 */
const BAND_TEXT: Record<ScoreBand, string> = {
  low: 'text-[var(--color-error)]',
  medium: 'text-[var(--color-warning)]',
  high: 'text-[var(--color-success)]',
}

/**
 * One signal, as a labelled 0-100 meter.
 *
 * `ProgressBar` rather than a number and a div: it is the design system's
 * component for a determinate 0-max value, it maps `variant` to the status
 * tokens, and it renders `role=progressbar` with `aria-valuenow`/`valuemax`
 * and its label as the accessible name. A hand-rolled bar would have had to
 * reproduce all of that to be readable by anything but an eye.
 */
export function CategoryScore({ item }: { item: ScoreItem }) {
  const value = categoryScore(item)
  return (
    <ProgressBar
      label={SIGNAL_LABELS[item.signal]}
      value={value}
      max={100}
      variant={BAND_VARIANT[scoreBand(value)]}
      hasValueLabel
      // Bare, not "50%". Everything on this page is now out of 100, and a
      // percent sign next to a total that is a weighted score would suggest
      // the two are the same measurement.
      formatValueLabel={(shown) => String(shown)}
    />
  )
}

/** The lead's total: the figure, in its band, over a meter on the same scale. */
export function ScoreTotal({ total }: { total: number }) {
  const band = scoreBand(total)
  return (
    <div className="flex flex-col gap-2">
      <span data-score-total data-score-band={band} className={BAND_TEXT[band]}>
        {/* `color="inherit"` so the band on the wrapper reaches the figure:
            Text otherwise paints its own primary colour over it. */}
        <Text as="span" type="display-2" color="inherit">
          {total}
        </Text>
      </span>
      {/* The heading above already names it, so the label is for assistive
          tech only rather than repeated on screen. */}
      <ProgressBar label="Interest score" isLabelHidden value={total} max={100} variant={BAND_VARIANT[band]} />
    </div>
  )
}

/**
 * The list's Score cell.
 *
 * A compact numeral, not a display heading: `display-3` made the score the
 * largest thing on the row (G4, "a huge 61"). It is data in a column, and the
 * theme's tabular figures are what make a column of them line up.
 */
export function ListScore({ total }: { total: number }) {
  const band = scoreBand(total)
  return (
    <span data-score data-score-band={band} className={`text-[15px] font-medium ${BAND_TEXT[band]}`}>
      {total}
    </span>
  )
}
