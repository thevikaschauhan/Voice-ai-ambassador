'use client'

import type { TalkLine } from '@/lib/talk/session'

/**
 * The transcript as subtitles, not as a log.
 *
 * A log asks the visitor to read while somebody is talking to them. Subtitles
 * carry the line being spoken now, large and centred, with the one before it
 * small and faded above - which is what a person glances at rather than reads.
 *
 * The history is not thrown away: everything earlier is behind a disclosure,
 * so a visitor who wants the whole conversation can have it and a visitor in
 * the middle of a sentence is not given a wall of text.
 *
 * Built on the rail #78 established: lines are already segment-keyed and
 * already correct for both sides (the visitor's arrive as whole texts, the
 * ambassador's as deltas), so this component only decides what is shown.
 */
export function TalkSubtitles({
  lines,
  name,
  idle,
}: {
  lines: readonly TalkLine[]
  /** Prefixes the ambassador's lines, so who is speaking needs no caption. */
  name: string
  /** What to say when nothing has been transcribed yet. */
  idle: string
}) {
  const current = lines.length > 0 ? lines[lines.length - 1] : null
  const previous = lines.length > 1 ? lines[lines.length - 2] : null
  const earlier = lines.slice(0, Math.max(0, lines.length - 2))

  return (
    <section
      aria-live="polite"
      aria-label="Transcript"
      className="flex w-full max-w-[62ch] flex-col items-center gap-3 text-center"
    >
      {previous !== null ? (
        <p className="text-[12px] leading-relaxed text-ink-600">
          <Speaker line={previous} name={name} />
          {previous.text}
        </p>
      ) : null}

      {current !== null ? (
        <p
          className={`text-[17px] leading-snug sm:text-[19px] ${
            current.final ? 'text-ink-100' : 'text-ink-300'
          }`}
        >
          <Speaker line={current} name={name} />
          {current.text}
        </p>
      ) : (
        <p className="text-[13px] text-ink-500">{idle}</p>
      )}

      {earlier.length > 0 ? (
        <details className="w-full text-left">
          <summary className="cursor-pointer list-none text-center text-[11px] tracking-[0.12em] text-ink-600 uppercase hover:text-brass-400">
            Earlier in this call ({earlier.length})
          </summary>
          <ol className="mt-3 flex flex-col gap-2">
            {earlier.map((line) => (
              <li key={line.id} className="text-[12px] leading-relaxed text-ink-500">
                <Speaker line={line} name={name} />
                {line.text}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </section>
  )
}

/**
 * Who said it. The ambassador is named; the visitor is "You".
 *
 * A name rather than a role for her, because that is the point of giving her
 * one - and it is the same name the orb carries, from the same source.
 */
function Speaker({ line, name }: { line: TalkLine; name: string }) {
  return (
    <span className="mr-2 text-[11px] tracking-[0.12em] text-ink-500 uppercase">
      {line.speaker === 'agent' ? name : 'You'}
    </span>
  )
}
