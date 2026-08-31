import type { Project } from '@/lib/types'

/**
 * Pure helpers over inventory records, safe on both sides of the boundary.
 * The loader itself is server-only (`lib/inventory.ts`) because it reads the
 * file; these two only shape what it returned.
 */

/**
 * Resolve shortlist ids against inventory, preserving the agent's order.
 *
 * An id with no record is reported rather than dropped: the agent checks
 * shortlist ids against inventory, so an unresolved one on screen means that
 * check has a hole, and hiding it would hide the defect.
 */
export function resolveShortlist(
  ids: readonly string[],
  projects: readonly Project[],
): { resolved: Project[]; unresolved: string[] } {
  const byId = new Map(projects.map((p) => [p.id, p]))
  const resolved: Project[] = []
  const unresolved: string[] = []
  for (const id of ids) {
    const project = byId.get(id)
    if (project === undefined) unresolved.push(id)
    else resolved.push(project)
  }
  return { resolved, unresolved }
}

/**
 * The milestone amounts `inventory.derive()` computes at load time.
 *
 * AGENTS.md invariant 2 says derived figures are computed, never
 * hand-authored - a hand-typed derived number is exactly the class of error
 * this system exists to prevent - so the UI applies the same percentage to the
 * same source price rather than carrying a table of its own.
 */
export function milestoneAmounts(
  project: Project,
): { label: string; pct: number; aed: number }[] {
  const price = project.price_from_aed
  if (project.payment_plan === null || price === null) return []
  return project.payment_plan.map((m) => ({
    label: m.label,
    pct: m.pct,
    aed: Math.round(price * (m.pct / 100)),
  }))
}
