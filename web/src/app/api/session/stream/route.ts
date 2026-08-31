import { openBridge } from '@/lib/bridge/client'
import { BridgeUnavailable, handshakePath } from '@/lib/bridge/handshake'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

/**
 * The browser's only door to the live event stream.
 *
 * The shape of the whole thing, and the reason it has three hops rather than
 * one:
 *
 *   agent (127.0.0.1, token required)  ->  this route (holds the token)
 *                                      ->  browser (same-origin, no token)
 *
 * The browser cannot be given the token, because a token in a browser is a
 * token in every page that shares it and in the devtools of anyone standing
 * behind the laptop. So the credential stops here, and what crosses to the
 * client is events - which the client is allowed to see, since it is about to
 * render them.
 *
 * Server-sent events rather than a websocket: the stream is one-directional by
 * design (the bridge is read-only, so there is nothing for the browser to say
 * back), and SSE reconnects on its own, which is what a demo laptop's sleeping
 * wifi needs.
 */
export async function GET(request: Request): Promise<Response> {
  if (handshakePath() === null) {
    // Not an error. The bridge is off unless the agent was told to write a
    // handshake, so this is the ordinary "no live call configured" answer and
    // the surface falls back to replay.
    return Response.json({ live: false, reason: 'bridge not configured' }, { status: 503 })
  }

  let bridge: Awaited<ReturnType<typeof openBridge>>
  try {
    bridge = await openBridge(request.signal)
  } catch (error) {
    const reason =
      error instanceof BridgeUnavailable ? error.message : 'cannot reach the agent'
    return Response.json({ live: false, reason }, { status: 503 })
  }

  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: string, data: string) => {
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${data}\n\n`))
      }
      try {
        send('open', '{"live":true}')
        for await (const line of bridge.lines) {
          if (request.signal.aborted) break
          // Forwarded verbatim. This route does not reshape the agent's events:
          // the reducer on the other side is typed off the agent's own schema,
          // so a translation layer here would be a second place for that
          // contract to drift.
          send('agent', line)
        }
        send('close', '{"reason":"the agent closed the stream"}')
      } catch (error) {
        const reason = error instanceof Error ? error.message : 'stream failed'
        send('close', JSON.stringify({ reason }))
      } finally {
        bridge.close()
        controller.close()
      }
    },
    cancel() {
      bridge.close()
    },
  })

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-store, no-transform',
      connection: 'keep-alive',
      // The stream carries buyer transcripts. Nothing between here and the
      // browser may keep a copy.
      'x-accel-buffering': 'no',
    },
  })
}
