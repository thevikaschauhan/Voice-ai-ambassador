'use client'

import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

/**
 * The orb: what a visitor looks at while they talk.
 *
 * A black disc on a dark field with a coloured corona around its rim - white
 * hot at the top, cyan and blue through the upper right, magenta and red down
 * the left, a warm yellow-green at the lower left. Built from layered
 * gradients and blur rather than from the reference image, so it scales, it
 * animates, and there is no asset with unknown provenance in the repository.
 *
 * The corona carries the STATE, because that is the job: a visitor with
 * nothing to look at cannot tell a thinking pause from a broken demo. It
 * breathes slowly while the call listens, blooms with the ambassador's own
 * voice while she speaks, tightens and cools while the visitor speaks, and
 * drifts while a turn is being thought about.
 *
 * Two layers rather than one, and the reason is in the reference: the rim is a
 * hard bright edge and the halo is a wide soft field, so a single blur cannot
 * be both. The inner layer is lightly blurred and sits just outside the disc;
 * the outer is heavily blurred and much larger, at low opacity.
 */

export type OrbState = 'idle' | 'listening' | 'speaking' | 'visitor' | 'thinking'

/** What the state is called under the orb, so the page says it as well as shows it. */
export const ORB_LABELS: Record<OrbState, string> = {
  idle: 'Ready',
  listening: 'Listening',
  speaking: 'Speaking',
  visitor: 'Hearing you',
  thinking: 'Thinking',
}

export function TalkOrb({
  state,
  level,
  name,
}: {
  state: OrbState
  /** 0 to 1, the speaking side's smoothed level. Drives the bloom. */
  level: number
  /** The ambassador's name, shown under the orb. */
  name: string
}) {
  const still = usePrefersReducedMotion()

  // The bloom. `level` only reaches the orb while somebody is speaking, so a
  // listening orb sits at its base size and a loud syllable pushes it out.
  const speaking = state === 'speaking' || state === 'visitor'
  const bloom = still || !speaking ? 0 : Math.min(1, level)

  // The visitor's turn reads cooler and tighter than the ambassador's: the same
  // corona, rotated so its cyan face leads, and held closer to the rim. That
  // way "who is talking" is legible without a caption, but it is still one
  // object rather than two different animations.
  const rotation = state === 'visitor' ? 200 : state === 'thinking' ? 90 : 0
  const spread = state === 'visitor' ? 0.7 : 1

  return (
    <div className="flex flex-col items-center gap-5">
      <div
        className="relative grid place-items-center"
        style={{ width: 'min(68vw, 340px)', height: 'min(68vw, 340px)' }}
        data-orb-state={state}
      >
        {/* The wide, soft halo. */}
        <span
          aria-hidden
          className={`absolute inset-[-6%] rounded-full ${
            still ? '' : state === 'thinking' ? 'orb-drift' : 'orb-spin'
          }`}
          style={{
            background: CORONA,
            filter: `blur(${26 + bloom * 16}px)`,
            opacity: (state === 'idle' ? 0.16 : 0.3) * spread + bloom * 0.26,
            transform: `rotate(${rotation}deg) scale(${1 + bloom * 0.06})`,
            transition: 'opacity 140ms linear, filter 140ms linear, transform 140ms linear',
          }}
        />
        {/* The bright rim, just outside the disc. */}
        <span
          aria-hidden
          className={`absolute inset-[6%] rounded-full ${
            still ? '' : state === 'thinking' ? 'orb-drift-slow' : 'orb-spin-slow'
          }`}
          style={{
            background: CORONA,
            filter: `blur(${8 + bloom * 6}px)`,
            opacity: (state === 'idle' ? 0.42 : 0.78) * spread + bloom * 0.18,
            transform: `rotate(${rotation}deg) scale(${1 + bloom * 0.035})`,
            transition: 'opacity 140ms linear, filter 140ms linear, transform 140ms linear',
          }}
        />
        {/* The disc. Pure black at its centre, as in the reference, with the
            faintest lift towards the top so it reads as a sphere rather than a
            hole. */}
        <span
          aria-hidden
          className={`absolute inset-[9%] rounded-full ${
            still || speaking ? '' : state === 'idle' ? '' : 'orb-breathe'
          }`}
          style={{
            background: '#000',
            // Just enough interior to read as a sphere rather than a hole, and
            // no more: the reference disc is black, and any lift beyond this
            // turns it grey against a black page.
            boxShadow: 'inset 0 2px 30px rgba(190,215,255,0.05)',
            transform: `scale(${1 + bloom * 0.028})`,
            transition: 'transform 140ms linear',
          }}
        />
      </div>

      <div className="flex flex-col items-center gap-1">
        <p className="text-[13px] tracking-[0.02em] text-ink-200">{name}</p>
        {/* The state in words as well as in light: this is what a
            reduced-motion visitor reads instead of the movement, and it is
            what a screen reader gets either way. */}
        <p
          className="text-[11px] tracking-[0.14em] text-ink-500 uppercase"
          role="status"
          aria-live="polite"
        >
          {ORB_LABELS[state]}
        </p>
      </div>
    </div>
  )
}

/**
 * The corona, read off the reference by angle: white-hot at the top, cyan and
 * blue through the upper right, magenta into red down the left, a warm
 * yellow-green at the lower left, and back to blue at the bottom.
 */
const CORONA = [
  'conic-gradient(from 336deg,',
  // The white-hot arc at the top, which is what makes it an eclipse rather
  // than a ring of colour.
  'rgba(255,255,255,0.95) 0deg,',
  'rgba(150,225,255,0.9) 26deg,',
  'rgba(40,120,255,0.62) 66deg,',
  // The left side falls away almost to nothing in the reference. Keeping these
  // dim is what stops the whole thing reading as a rainbow.
  'rgba(120,60,220,0.3) 108deg,',
  'rgba(255,60,150,0.62) 156deg,',
  'rgba(255,70,60,0.5) 196deg,',
  'rgba(120,60,90,0.16) 232deg,',
  'rgba(190,255,170,0.34) 274deg,',
  'rgba(60,200,255,0.72) 316deg,',
  'rgba(255,255,255,0.95) 360deg)',
].join(' ')
