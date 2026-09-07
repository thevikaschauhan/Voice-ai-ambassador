'use client'

import { createContext, use, type ReactNode } from 'react'

/**
 * The build identity, carried from the server layout to the shell's footer.
 *
 * WHY A CONTEXT AND NOT A PROP. The footer lives in `AdminAppShell`, which
 * every page renders, so passing the sha as a prop would mean every page
 * reading it and passing it on. That is the shape of the regression this
 * codebase already paid for once: wrapping the pages in the shell dropped the
 * per-page `<header>` and all five admin routes lost their `<h1>` at the same
 * time, because a rule each page has to remember is a rule some page will
 * forget. The layout reads the environment once and the shell reads the
 * context; no page is involved and no page can get it wrong.
 *
 * The default is `null`, which is the same answer an unset variable gives, so
 * a shell rendered outside the provider omits the line rather than throwing.
 */
const BuildInfoContext = createContext<string | null>(null)

export function BuildInfoProvider({
  commitSha,
  children,
}: {
  /** The full sha, or null when the platform set none. */
  commitSha: string | null
  children: ReactNode
}) {
  return <BuildInfoContext value={commitSha}>{children}</BuildInfoContext>
}

/** The full sha this service was built from, or null. */
export function useCommitSha(): string | null {
  return use(BuildInfoContext)
}
