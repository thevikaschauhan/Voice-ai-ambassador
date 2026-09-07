/**
 * Which commit the web service was built from, or nothing.
 *
 * Railway sets `RAILWAY_GIT_COMMIT_SHA` on every build and deployment - "the
 * git SHA of the commit that triggered the deployment" - so there is nothing
 * to mint, no build argument to plumb through the Dockerfile and no
 * `NEXT_PUBLIC_` copy to inline. `app/admin/layout.tsx` is a server component
 * and reads it at request time.
 *
 * "BUILT FROM", NOT "RUNNING", and the footer says so. The web service
 * redeploys on `web/**` and `data/**` only (.railway/railway.ts
 * watchPatterns), so a merge that touches neither produces a SKIPPED
 * deployment and this sha stays at the previous commit. That is not a bug to
 * paper over: it is the true identity of the running image.
 *
 * WHICH HALF OF THE SWEEP LINE THIS IS. The deploy sweeps print
 * "web <deployment id> on <commit>", so the footer's value is the SECOND
 * token, the commit - not the first. An earlier version of this comment
 * pointed at the deployment id, which would send the next reader comparing the
 * footer against the wrong half of the line and concluding the page was
 * lying. Wording the footer "built from" is what makes it and the sweep's
 * commit agree.
 *
 * ABSENT IS A REAL ANSWER. Locally, in CI and in every test there is no such
 * variable, and the footer omits its line rather than showing "dev",
 * "unknown" or an empty space. A placeholder is worse than silence: a reviewer
 * who reads "dev" on a deployed page learns something false, where a reviewer
 * who sees no line learns only that the page is not telling them - which is
 * true. Never add a fallback string to `commitSha`.
 *
 * NOT MARKED `server-only`, deliberately, and this is the one tradeoff in the
 * file. `commitSha` is server-side by nature, but `shortSha` is a pure string
 * function that the shell's footer needs, and the shell is a client component;
 * a `server-only` import here would make that impossible. The guard is instead
 * that `commitSha` has exactly one caller, the admin layout, and that calling
 * it in a browser degrades to `null` and an omitted line rather than to a
 * crash or a leak - the value is a commit sha, not a secret.
 */

/** The full sha the platform set, or null when it set nothing usable. */
export function commitSha(): string | null {
  const value = process.env.RAILWAY_GIT_COMMIT_SHA?.trim()
  // A blank or whitespace-only variable counts as unset: the platform sets
  // this, but a hand-edited service variable can be empty, and `''` would
  // render a footer line with nothing in it - which reads as broken rather
  // than as absent.
  return value === undefined || value === '' ? null : value
}

/**
 * The first seven characters, which is what a human reads and types.
 *
 * The FULL sha stays in the rendered element's `title`, so nobody on the floor
 * has to retype a prefix into a `git show`. A sha shorter than seven comes
 * back whole rather than padded: it is not this function's job to decide the
 * platform lied about the format.
 */
export function shortSha(sha: string): string {
  return sha.slice(0, 7)
}
