/**
 * How long ago, in words a reviewer scans rather than reads.
 *
 * The exact instant is never replaced by this - every call site keeps it in
 * `dateTime` and on `title` - so nothing machine-readable is lost and the
 * precise time is one hover away. This is the scanning layer on top.
 *
 * IT DEGRADES RATHER THAN THROWING. An unparseable timestamp comes back as the
 * raw string, because a list that renders "Invalid Date" in a column is worse
 * than one that shows what the API actually sent, and a list that throws takes
 * the whole page down over a formatting concern. Same rule as the mappers in
 * `leads.server.ts`.
 *
 * A FIXED "NOW" IS NOT AVAILABLE HERE and that is deliberate: these run during
 * render on the client, so `Date.now()` is the only honest reading. The tests
 * assert the exact timestamp through `title` and `dateTime` rather than the
 * relative words, so they do not depend on when they are run.
 */
export function relativeAge(iso: string, now: number = Date.now()): string {
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return iso

  const seconds = Math.round((now - at) / 1000)
  // A clock skew between the browser and the writer can put a timestamp in the
  // future by a few seconds. "in 3 seconds" would be technically right and
  // read as a bug, so anything under a minute either way is "just now".
  if (seconds < 60) return 'just now'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  // Beyond a month the relative form stops helping - "4 months ago" is not
  // something a reviewer can act on - so it becomes the date.
  return new Date(at).toISOString().slice(0, 10)
}
