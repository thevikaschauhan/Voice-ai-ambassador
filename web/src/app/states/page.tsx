import Link from 'next/link'
import { AmbassadorView } from '@/components/ambassador-view'
import { SessionBanner } from '@/components/session-banner'
import { TranscriptRail } from '@/components/transcript-rail'
import { loadInventory } from '@/lib/inventory'
import { DESIGNED_STATES } from '@/lib/session/designed-states'

export const dynamic = 'force-dynamic'

/**
 * Every escalation and failure state, side by side.
 *
 * These are the states the demo must not meet for the first time on stage.
 * Each one is folded from a real event sequence through the live reducer, so
 * this page cannot drift from what the call surface would actually render.
 */
export default async function StatesPage() {
  const projects = await loadInventory()

  return (
    <main className="mx-auto flex min-h-screen max-w-[1280px] flex-col gap-10 px-4 py-6 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
        <div>
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">
            Designed states
          </h1>
          <p className="mt-1.5 max-w-[74ch] text-[12px] leading-relaxed text-ink-500">
            Escalation and failure, rendered from real event sequences through the same
            reducer the call surface uses. Nothing here is a mock-up.
          </p>
        </div>
        <Link className="text-[12px] text-ink-400 hover:text-brass-400" href="/">
          Call surface
        </Link>
      </header>

      {DESIGNED_STATES.map((designed) => (
        <section key={designed.id} className="space-y-4">
          <div>
            <h2 className="text-[14px] text-ink-100">{designed.title}</h2>
            <p className="mt-1.5 max-w-[86ch] text-[12px] leading-relaxed text-ink-400">
              {designed.why}
            </p>
          </div>
          <SessionBanner state={designed.state} />
          <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
            <TranscriptRail turns={designed.state.turns} />
            <AmbassadorView state={designed.state} projects={projects} />
          </div>
        </section>
      ))}
    </main>
  )
}
