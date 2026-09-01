import 'server-only'

import { spawn } from 'node:child_process'
import type { ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import type { AgentEvent } from '@/lib/session/events'
import type { TextCore, TextTurnInput } from '@/lib/textmode/core'

/**
 * Text mode, backed by the actual Python core.
 *
 * docs/01- makes this the venue plan B and the word carrying the claim is
 * SAME: the same prompts, the same guardrail, the same recovery policy and the
 * same escalation routing as a call, exercised through `adapter/textmode.py`.
 * The fixture this replaces looked identical on screen and proved nothing.
 *
 * ONE PROCESS, NOT ONE PER TURN. A turn is not independent - the confirmation
 * coordinator carries call state that cannot be rebuilt from the transcript,
 * so the session lives as long as the process does. It is started lazily on
 * the first turn, because a server that boots an agent subprocess nobody asked
 * for is a server that spends money at idle.
 *
 * The credentials stay where they already are: the child reads `agent/.env`
 * itself, so no key is passed through this tier and none can reach the
 * browser.
 */

/** Long enough for a model call plus the brief extraction behind it. */
const TURN_TIMEOUT_MS = 60_000

/** Where the agent lives. Its presence is what selects the real core. */
export function agentDir(): string | null {
  const dir = process.env.AMBASSADOR_AGENT_DIR?.trim()
  return dir ? dir : null
}

class CoreProcess {
  private child: ChildProcessWithoutNullStreams | null = null
  private pending: ((line: string) => void)[] = []
  private stderr = ''

  constructor(private readonly cwd: string) {}

  private ensure(): ChildProcessWithoutNullStreams {
    if (this.child !== null && this.child.exitCode === null) return this.child

    const child = spawn('uv', ['run', '--no-sync', 'python', '-m', 'adapter.textmode'], {
      cwd: this.cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    // Kept and surfaced on failure. A core that died at import time says why
    // on stderr, and swallowing that leaves an operator with a blank chat and
    // nothing to read.
    child.stderr.on('data', (chunk: Buffer) => {
      this.stderr = (this.stderr + chunk.toString()).slice(-2000)
    })
    createInterface({ input: child.stdout }).on('line', (line) => {
      const resolve = this.pending.shift()
      if (resolve !== undefined) resolve(line)
    })
    child.on('exit', () => {
      // Fail every waiter rather than leaving the page spinning: the route
      // turns a rejection into the composed handover, and a hung request into
      // nothing at all.
      const waiting = this.pending
      this.pending = []
      for (const resolve of waiting) resolve('')
    })
    this.child = child
    return child
  }

  async request(payload: object): Promise<unknown> {
    const child = this.ensure()
    const line = new Promise<string>((resolve, reject) => {
      this.pending.push(resolve)
      setTimeout(() => reject(new Error('the core did not answer in time')), TURN_TIMEOUT_MS)
    })
    child.stdin.write(JSON.stringify(payload) + '\n')
    const answer = await line
    if (answer === '') {
      throw new Error(
        `the core exited${this.stderr ? `: ${this.stderr.trim().split('\n').slice(-3).join(' ')}` : ''}`,
      )
    }
    return JSON.parse(answer)
  }
}

let running: CoreProcess | null = null

export function processTextCore(cwd: string): TextCore {
  running ??= new CoreProcess(cwd)
  const core = running
  return {
    async turn({ text }: TextTurnInput): Promise<AgentEvent[]> {
      // One line in, one line out. The core folds its opening events into the
      // first turn's response, so there is no "is this the first request"
      // state to keep on this side.
      const answer = (await core.request({ text })) as
        | { events?: AgentEvent[]; error?: string }
        | null
      if (answer?.error !== undefined) throw new Error(answer.error)
      return answer?.events ?? []
    },
  }
}
