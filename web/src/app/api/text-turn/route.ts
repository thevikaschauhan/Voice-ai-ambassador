import { NextResponse } from 'next/server'
import { replayTextCore } from '@/lib/textmode/core'
import { agentDir, processTextCore } from '@/lib/textmode/process'

export const runtime = 'nodejs'

/**
 * The text-mode turn endpoint.
 *
 * It exists so the browser never talks to a provider (AGENTS.md hard rule):
 * whatever is behind `TextCore` runs on this side of the wire, with the keys.
 *
 * Which one is behind it is decided by whether `AMBASSADOR_AGENT_DIR` names an
 * agent, the same opt-in shape the events bridge uses. The page says which it
 * got, because a fixture that looks like the real pipeline is the one mistake
 * this surface must never make.
 */
function core() {
  const dir = agentDir()
  return dir === null ? replayTextCore() : processTextCore(dir)
}

interface Body {
  sessionId?: unknown
  turnIndex?: unknown
  text?: unknown
}

export async function POST(request: Request): Promise<NextResponse> {
  let body: Body
  try {
    body = (await request.json()) as Body
  } catch {
    return NextResponse.json({ error: 'body must be JSON' }, { status: 400 })
  }

  const text = typeof body.text === 'string' ? body.text.trim() : ''
  const turnIndex = typeof body.turnIndex === 'number' ? body.turnIndex : NaN
  const sessionId = typeof body.sessionId === 'string' ? body.sessionId : ''

  if (text === '') {
    return NextResponse.json({ error: 'text is required' }, { status: 400 })
  }
  if (!Number.isInteger(turnIndex) || turnIndex < 1) {
    return NextResponse.json({ error: 'turnIndex must be a positive integer' }, { status: 400 })
  }

  try {
    const events = await core().turn({ sessionId, turnIndex, text })
    return NextResponse.json({ events })
  } catch (error) {
    // The page turns this into the composed handover, so a buyer never sees
    // silence. The reason is carried for whoever is reading the console.
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'the core failed' },
      { status: 502 },
    )
  }
}
