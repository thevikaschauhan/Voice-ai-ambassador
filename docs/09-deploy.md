# 09 - Deployment

## The topology in one paragraph

We host, and Railway is the platform. Media transport does not move: it stays on LiveKit Cloud, because ADR-005 chose managed WebRTC and because Railway has no UDP ingress, so it could not serve a media server even if we wanted it to. Railway runs exactly two services in one project and one environment: `agent-worker`, the Python LiveKit Agents worker, which is outbound only and exposes no port; and `web`, the Next.js surface, which takes the public domain and mints its own viewer tokens in a server route. Secrets live in Railway service variables and nowhere else. Scope for all of this is the ships table in `docs/06-`; the demo choreography is `docs/07-demo-runbook.md`.

## Media stays on LiveKit Cloud

This is a constraint, not a preference, and it is worth saying out loud to anyone who asks why we did not "just host everything".

WebRTC media is UDP. Railway's ingress is HTTP and TCP; there is no UDP path to a container, so a media server placed there would have nothing to receive audio on. ADR-005 had already chosen LiveKit Cloud for managed transport, on the grounds that WebRTC survives a hostile venue network far better than a raw WebSocket does, and that decision is what makes this a non-question rather than a compromise.

The practical consequence: `agent-worker` and the browser both connect *out* to LiveKit Cloud and meet in a room there. Neither one connects to the other, and there is no audio path through Railway at all.

## Two services, one project

| Service | What runs | Network | Entry point |
|---|---|---|---|
| `agent-worker` | The LiveKit Agents worker, Python | Outbound only. No port, no public domain | `cli.run_app` in `agent/src/adapter/agent.py` |
| `web` | The Next.js app, Node | Public Railway domain | `npm run build`, then `npm run start` |

### `agent-worker`

The worker registers with LiveKit Cloud and waits to be given a job; when a call starts it joins the room. Every provider it needs (LiveKit, OpenRouter, Deepgram, Fish) it reaches over HTTPS on its own initiative. Nothing ever calls *in*.

That is the security property worth naming: the service has no listening socket, so it has no ingress surface to reason about. It needs no domain, no port, and no health-check route, and it should be given none. A public domain on this service would be a hole with nothing behind it.

Startup is fail-fast by design. `Settings.missing_for_voice()` names every credential the voice path cannot start without, by name and never by value, so a service deployed with a variable missing says which one during preflight rather than failing on the first sentence of a call.

### `web`

The browser gets a listen-only ticket to the call in progress, and the route that mints it is `web/src/app/api/session/room/route.ts`. What crosses the wire is the signalling URL, a room name, and a token that expires in ten minutes and can do exactly one thing: subscribe to audio in one named room. The API secret that signs it stays on the server.

This is why there is no separate token service: minting is one Next server route using `livekit-server-sdk`, which is the same tier the rest of the app's server work already happens in. It also satisfies the hard rule in AGENTS.md that no provider is ever called from the browser.

A `web` deployed without LiveKit variables does not crash. `api/session/room` answers 503 with an honest reason and the surface keeps its "no audio track" label, which is the correct behaviour for a machine that has no call to show.

## Secrets and the environment contract

Railway service variables only. Keys never enter the repository; that rule predates hosting and hosting does not relax it.

**The environment contract is `agent/.env.example`, by reference.** It is not reproduced here, deliberately. A second copy of a variable list drifts from the first, and the copy in `.env.example` is the one a reader can trust because it sits beside the loader that reads it. That file already anticipated this move: it says these same variables go into the Railway service environment.

One gap a deployer will hit, recorded rather than smoothed over. `agent/.env.example` is the *agent's* contract, and the `web` service reads three variables that are not in it:

| Variable | Read by | What it is for |
|---|---|---|
| `LIVEKIT_ROOM` | `web/src/lib/livekit/room.ts` | Pins the room name instead of letting the server pick one |
| `AMBASSADOR_AGENT_DIR` | `web/src/lib/textmode/process.ts` | Path to the agent checkout, for text mode |
| `AMBASSADOR_BRIDGE_HANDSHAKE` | `web/src/lib/bridge/handshake.ts` | Path to the event bridge's handshake file |

The last two are same-host paths, and the section below is about what that means. Writing the web contract down is `task-railway-web-service`; this table is the current state, not the destination.

## Build reproducibility

Both images should mirror `.github/workflows/gates.yml` rather than inventing their own toolchain, so that a green pull request means something about the thing that gets deployed.

For `agent-worker` that means uv 0.10.7, Python 3.14, and `uv sync --frozen`. The `--frozen` is not a style preference: `agent/pyproject.toml` sets `exclude-newer = "5 days"`, which is a *moving* window, so an unfrozen build resolves a different dependency set depending on the day it runs. A deployment that cannot be reproduced next week is not a deployment, it is a snapshot.

## Open: what the two-service split breaks

Two of the web surface's features are same-host by construction, and putting `web` and `agent-worker` in separate containers ends both. Neither is a defect in the code; both are deliberate designs that assumed one machine. Recording them here because `task-railway-web-service` and `task-railway-agent-service` will both walk into them.

**Text mode.** `web/src/lib/textmode/process.ts` spawns `uv run python -m adapter.textmode` as a child process in `AMBASSADOR_AGENT_DIR`. The `web` container is a Node image: no Python, no uv, no copy of `agent/`. So text mode cannot run there. This matters more than a missing panel, because text mode is in the ships table as the venue plan B and `docs/07-demo-runbook.md` lists it as the first recovery when audio fails in the room.

**The live event stream.** The ambassador view's transcript, latency meter and guardrail decisions arrive over the event bridge, and the bridge is loopback-only on purpose: the agent binds nothing but localhost, the handshake is a 0600 file on a shared filesystem, and `handshake.ts` refuses any host that is not loopback so a rewritten handshake cannot point the token somewhere else. Two Railway services share neither a filesystem nor a loopback interface. The restriction is a security property, so the answer is not to relax it.

Both are tracked as **issue #63**, which is the citation to use from a build pull request rather than this heading.

The question they raise is a scoping one, and it is not mine to answer: **is the hosted stack what the meeting is demonstrated from, or a persistent environment that exists alongside a laptop demo?** If the demo is driven from a laptop, both features keep working there and the hosted `web` is a public read-only view that is honestly missing two panels. If the demo is driven from the hosted URL, then the bridge and text mode need a transport between services rather than files and subprocesses, and that is design work nobody has scoped, touching a security property deliberately.

The decision sits with the human as `task-railway-demo-source`. Until it is answered, neither build card should invent an answer: both build the current shape.

## Not deployed

One project, one environment, so there is no staging tier. No custom domain: the generated Railway domain is the demo URL. No autoscaling and no replica count above one; a single concurrent call is what the demo needs and a second worker replica would race for the same job. No database, which is not a hosting decision at all, just the existing one (in-memory session state, `STUB:` on the CRM write). Web gates in CI are still absent and still a separate decision, noted in `gates.yml` itself.

## Where the rest lives

- Scope, and whether a thing ships at all: `docs/06-`
- Why LiveKit Cloud, and the adapter boundary: ADR-005 and ADR-002 in `docs/01-`
- The variable list: `agent/.env.example`
- What a call costs to run: `docs/08-`
- Running the demo on the day: `docs/07-demo-runbook.md`
