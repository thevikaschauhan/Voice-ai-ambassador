import { NextResponse } from 'next/server'
import { replayTextCore } from '@/lib/textmode/core'

export const runtime = 'nodejs'

/**
 * The text-mode turn endpoint.
 *
 * It exists so the browser never talks to a provider (AGENTS.md hard rule):
 * whatever ends up behind `TextCore` - today a fixture, in milestone two the
 * framework-free Python core - runs on this side of the wire, with the keys.
 */
const core = replayTextCore()

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

  const events = await core.turn({ sessionId, turnIndex, text })
  return NextResponse.json({ events })
}
