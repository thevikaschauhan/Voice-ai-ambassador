/**
 * Presentation only. No figure is derived here that the agent has not already
 * computed - formatting a number is not arithmetic on it (AGENTS.md
 * invariant 2).
 */

/**
 * Grouping is pinned to one locale on purpose.
 *
 * `Number.toLocaleString()` with no locale follows whoever is looking at the
 * screen, and under an Indian locale AED 2,000,000 renders as 20,00,000. A
 * figure that changes shape depending on the viewer is not a figure the room
 * can read back to you, and the lakh/crore confusion is one this project
 * already treats as a 10x hazard (docs/04-). Western grouping, ASCII space,
 * everywhere, regardless of the machine the demo runs on.
 */
const GROUPED = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

export function num(value: number): string {
  return GROUPED.format(value)
}

export function aed(value: number): string {
  return `AED ${num(value)}`
}

export function sqft(min: number | null, max: number | null): string | null {
  if (min === null && max === null) return null
  if (min !== null && max !== null) return `${num(min)} - ${num(max)} sq ft`
  return `${num((min ?? max)!)} sq ft`
}

export function quarter(q: number, year: number): string {
  return `Q${q} ${year}`
}

export function ms(value: number): string {
  if (value < 10) return `${value.toFixed(2)} ms`
  return `${num(value)} ms`
}

/** Percentile by nearest rank, on a copy. Used for the session latency line. */
export function percentile(values: readonly number[], p: number): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const rank = Math.ceil((p / 100) * sorted.length)
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank - 1))]
}

/**
 * A figure sourced from a record still carrying a VERIFY: marker is
 * illustrative, and the build plan requires it to be visibly marked rather
 * than presented as fact (R6, definition of done).
 */
export function isUnverified(sourceRef: string, lastVerified: string | null): boolean {
  return lastVerified === null || sourceRef.includes('VERIFY:')
}
