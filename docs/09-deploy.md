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

That is the security property worth naming: the DEPLOYMENT has no ingress surface to reason about. It needs no domain, no port, and no health-check route, and it should be given none. A public domain on this service would be a hole with nothing behind it. Precisely, though, the PROCESS is not socketless: the framework's own health server binds 8081 inside the container (`GET /` returns 200, or 503 when the inference process is dead or the LiveKit connection has failed; `GET /worker` returns worker JSON), and its port is a fixed prod default in `WorkerOptions` with no flag and no environment variable, so it could not follow a platform-injected `PORT` even if we wanted a probe. It is simply never published.

Startup is fail-fast by design, and it is enforced before the framework starts rather than left to it. `adapter.agent.preflight()` runs ahead of `cli.run_app` on the connecting subcommands (`start`, `dev`, `connect`) and calls `Settings.missing_for_worker()` - transport credentials plus the provider keys - naming every missing variable and never a value, then exiting non-zero. So a service deployed with a variable missing says which one during preflight rather than failing on the first sentence of a call.

Both halves of that had to be built, and the reasons are worth keeping. `missing_for_voice()` alone ran inside `entrypoint`, which only runs once a job is dispatched, so a worker with LiveKit credentials and no `FISH_API_KEY` registered, passed every check the platform could see, and failed on the first buyer. And the framework's own transport check cannot be relied on for the rest: with no credentials at all it logs "worker failed", drains, and exits ZERO, so a restart-on-failure policy never trips and a misconfigured deploy stops quietly on the dashboard. Console mode is deliberately exempt - it runs a mock job in a `console-room` and dials nothing, so demanding transport credentials there would refuse to start the text-mode fallback over keys it never uses.

The service's config is the root `railway.json`, and its `watchPatterns` cover `agent/**`, `data/**`, the `Dockerfile`, `railway.json` and `.dockerignore` - without them a web-only push rebuilds and redeploys the worker, which drains live calls for a change it does not contain. `data/**` appears in BOTH services' patterns on purpose: an inventory edit is a deploy for the worker as well as the web surface, because the worker reads the same files. It sets no `startCommand`: the image's `CMD` carries `--drain-timeout 600`, and a start command specified here would silently replace it and take the drain with it.

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

The last two are same-host paths, and the section below is about what that means. Writing the web contract down is `task-railway-web-service`; this table is the current state, and the section below is the destination.

## The `web` service contract

The table above is the inventory; this is the destination. Every value here is
verified against a running container, not read off the source.

| Variable | Railway value | If absent |
|---|---|---|
| `LIVEKIT_URL` | service variable | `api/session/room` answers 503 with a reason; the surface keeps its "no audio track" label |
| `LIVEKIT_API_KEY` | service variable | same |
| `LIVEKIT_API_SECRET` | service variable | same |
| `LIVEKIT_ROOM` | unset | the server picks a room name, which is the intended behaviour |
| `AMBASSADOR_AGENT_DIR` | **unset** | text mode serves the replay core (issue #63) |
| `AMBASSADOR_BRIDGE_HANDSHAKE` | **unset** | `api/session/stream` answers 503 `{"live":false,"reason":"bridge not configured"}` (issue #63) |
| `PORT` | injected by Railway | falls back to 3000, set in the image |
| `NODE_ENV` | `production`, set in the image | the npm scripts pin it anyway, and that pin is load-bearing |

The three LiveKit variables carry the same names the agent uses, so they are the
`agent/.env.example` entries by reference and are not re-listed with values.

**There are no `NEXT_PUBLIC_` variables, and that absence is the security
property rather than an omission.** Nothing the browser needs is a secret: it
receives a signalling URL, a room name and a ten-minute listen-only token minted
in the server route. A `NEXT_PUBLIC_` variable is compiled into the client
bundle, so introducing one to "make the URL available" would be the first step
of putting credentials in a page.

The last two variables must be left unset deliberately, not merely left blank by
oversight. Both are same-host paths (issue #63): setting `AMBASSADOR_AGENT_DIR`
in this container points at a directory with no Python, no uv and no `agent/`,
and `AMBASSADOR_BRIDGE_HANDSHAKE` would point at a file the other container
writes. Unset is the state each reader already treats as "off", so the surface
degrades to exactly what it can honestly show.

### The layout is part of the contract

`web/src/lib/inventory.ts` and `web/src/lib/readiness.ts` read
`join(process.cwd(), '..', 'data', ...)` at request time, and `next.config.ts`
sets `outputFileTracingRoot` to the repo root for the same reason. So the
container runs with cwd `/srv/web` and the data beside it at `/srv/data`. That
relative path is why this service is built from a Dockerfile with the **repo
root** as its build context rather than by Railway's own builder pointed at
`web/`: a `web/`-rooted build compiles cleanly and then answers 500 to every
page that needs a price, with `ENOENT: no such file or directory, open
'/data/inventory.json'`. That was reproduced before choosing, because a green
build that fails on stage is the worst available failure shape.

Two consequences for provisioning, both easy to get wrong:

- **Root Directory must stay unset.** Railway pulls only the files under a
  service's root directory, so setting it to `/web` removes `data/` from the
  build context and produces exactly the broken image above.
- **The service's config file is `/web/railway.json`**, set per service as an
  absolute path. Railway's config file does not follow Root Directory, and a
  root `railway.json` cannot describe two different services.

`railway.json` deliberately sets no `startCommand`: the image's `CMD` is
`npm run start`, so the start path is defined once, in the file that also pins
`NODE_ENV`. `healthcheckPath` is `/` rather than a dedicated endpoint because
`/` is the page that reads `../data`, so a mislayered image fails its health
check instead of serving broken prices. `watchPatterns` covers `web/**` and
`data/**`: an inventory edit is a deploy for this service.

### The trap in `next start`

`next start` loads `next.config.ts`, and a TypeScript config needs a TypeScript
compiler to read. With production-only dependencies installed, Next does not
fail with a missing module - it tries to **install TypeScript at boot, with
yarn, over the network, into the running container**, and the version it reaches
for is not the one this repo pins. It needs egress at start-up, it writes into
the image at runtime, and as a non-root user it simply fails. The image
therefore ships the pinned `typescript` from the same lockfile the build used.
Renaming the config to `.mjs` would also close it, and is deliberately not done:
it would trade a real `NextConfig` type for a JSDoc comment on a file that
several cards read.

## Build reproducibility

Both images should mirror `.github/workflows/gates.yml` rather than inventing their own toolchain, so that a green pull request means something about the thing that gets deployed.

For `agent-worker` that means uv 0.10.7, Python 3.14, and `uv sync --frozen`. The `--frozen` is not a style preference: `agent/pyproject.toml` sets `exclude-newer = "5 days"`, which is a *moving* window, so an unfrozen build resolves a different dependency set depending on the day it runs. A deployment that cannot be reproduced next week is not a deployment, it is a snapshot.

For `web` there was nothing to mirror: the four web gates are not in
`gates.yml`, so no CI job pins a Node version and the image had to choose one.
It pins Node 24, the active LTS, and installs with `npm ci` from
`package-lock.json` so the runtime runs what the lockfile pinned rather than
what the registry offers today. `--ignore-scripts` is safe in this tree because
the only package with a meaningful install script is `sharp`, for `next/image`,
and this app has no `next/image`. When the web gates do land in CI, the Node
version belongs in both places or in neither.
## Verifying a deploy

You have pasted the six secrets and Railway has redeployed. This section is how
you find out whether that worked. Most of the states below were produced on
purpose, against a production `web` build and the worker image from this tree;
the rest are read from the framework source at the version this repo pins. Which
is which is recorded at the end, because a verification procedure that cannot
say where its own expectations came from is just folklore.

**The one rule, and it holds for both services: the deploy status is not
evidence.** Railway shows you whether the container started, and both of these
services can start perfectly and still be useless. The worker can fail every
attempt to reach LiveKit and then exit *successfully*. The `web` healthcheck can
pass with every LiveKit variable missing. So there are two specific things to
look at instead, one per service, and neither of them is the green tick.

### `agent-worker`: you are looking for one log line

The deployment has no domain and no published port, by the design in the section
above, so there is nothing to curl from outside; the framework's own health
server binds 8081 inside the container and is never published, which is the
point of it. The liveness signal is a single line in the deploy log:

```
railway logs -s agent-worker -d
```

Up means a record whose message is `registered worker`, carrying the worker's
`id`, the `url` it registered against, and the `region`
(`livekit/agents/worker.py`). Until that line appears the worker is not in the
pool and no buyer can reach it, whatever the dashboard says.

Two details will trip you up if you go looking for the wrong shape:

- **The production log is JSON.** `setup_logging` installs a `JsonFormatter`
  whenever devmode is off, and the container's `CMD` runs the `start`
  subcommand, so `id` is a field in a JSON object and not the `id=...` suffix
  you get in a local dev run. Grep for `registered worker`, not for `id=`.
- **`railway logs` defaults to the most recent *successful* deployment**, or the
  latest one if none has succeeded. A crash-loop is not a successful deployment,
  so on a service that deployed cleanly last week the default view shows you
  *that* deployment and hides today's refusal entirely. Whenever the status is
  failed or crashed, pass the deployment id explicitly, or read that
  deployment's log in the dashboard.

**A missing variable is loud, and you do not need this section to catch it.**
`adapter.agent.preflight()` runs ahead of `cli.run_app`, names what it wants on
stderr, and exits 1. Measured in this image with an empty environment:

```
missing credentials for the voice path: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENROUTER_API_KEY, FISH_API_KEY
Set them in agent/.env (see agent/.env.example) or in the environment.
```

A non-zero exit is what `restartPolicyType: ON_FAILURE` in the root
`railway.json` is for, so the deploy crash-loops through its ten retries and
ends up **failed** on the dashboard, with the variable names in the log.

That was not true before #66, and the old behaviour is worth keeping in mind
because it is still the shape of the failure below: the framework's own path
logs `worker failed`, drains, and exits **0** (measured at the #64 gate), and a
clean exit never trips `ON_FAILURE`, so nothing retries and Railway reports a
healthy deployment sitting on a process that has already given up. Preflight
took the *missing* variable out of that path. It could not take the rest.

**So the case this check exists for is a variable that is present and wrong.**
Preflight tests presence, not validity: a credential that is complete nonsense
passes it and reaches the framework, where the quiet exit still lives. Measured
in this image, all six variables set to junk with `LIVEKIT_URL` pointing at an
unresolvable host:

- sixteen `failed to connect to livekit, retrying in Ns` warnings, each carrying
  the real cause in its `error` field,
- then `worker failed`, `RuntimeError: failed to connect to livekit after 16
  attempts`, and `draining worker` whose `id` reads `unregistered` rather than a
  real worker id,
- and **exit code 0**, two minutes and seventeen seconds after start.

Two consequences, and both of them bite. The exit code means Railway shows that
deploy as having stopped cleanly rather than crashed. The two minutes mean a
status check thirty seconds after pasting a bad key finds a container that is
running perfectly well, because it is still retrying. A wrong key against a real
LiveKit host takes the same road rather than a faster one: the connection and
the register handshake sit inside a single `except Exception` on that retry
counter, so a rejection is retried sixteen times and ends in the same silent
exit, with the refusal in the `error` field instead of a DNS failure.

Which is why this check is positive-only. **Do not verify the worker by looking
for a crash, because when a value is merely wrong there will not be one.**
Verify it by finding `registered worker`, and treat the absence of that line as
the failure whatever the deploy status says. The log then tells you
which of the two failures you have: names on stderr and a crash-loop is a
missing variable, sixteen retries ending in a drain marked `unregistered` is a
wrong one.

One limit on the positive case, so you do not read it as more than it says.
`registered worker` proves the worker reached LiveKit; it does not prove the
worker can hear. `STT_ENABLED` defaults to off, and with it off preflight does
not ask for `DEEPGRAM_API_KEY` at all, which is why it is absent from the five
names above. Getting the recogniser configured is the environment contract's
business rather than this check's, but a registered worker is not on its own a
worker that can take a call.

### `web`: the healthcheck proves the layout, not the configuration

`healthcheckPath` is `/`, and a 200 there is worth exactly one thing: the image
is layered correctly. `/` is a dynamic route that reads `../data` at request
time, so it cannot answer 200 unless `data/` really is sitting beside the app in
the container. That is the whole reason `/` was chosen over a dedicated endpoint,
and a mislayered image fails it with the `ENOENT` on `/data/inventory.json`
described in the contract above.

What a green healthcheck does **not** tell you is whether LiveKit is configured.
Measured on a production build with no LiveKit variables set at all: `GET /`
still answers **200**. The page renders from `../data` and the surface
honestly reports no audio track. So finish the check at the route that actually
reads the credentials.

### `web`: on `api/session/room`, the reason is the evidence

```
curl -i https://<your-railway-domain>/api/session/room
```

Read the `reason`, not the status code. Every string below is the literal
response body:

| Response | What it means |
|---|---|
| 503 `{"room":null,"reason":"no LiveKit room is open; the agent is not in a call"}` | **This is the pass.** All three variables are set and LiveKit accepted them; there is simply no call in progress yet. This is the correct answer for a healthy, idle deployment. |
| 200 `{"url":...,"token":...,"room":...}` | Also healthy, and a call is live right now. The token is listen-only and expires in ten minutes. |
| 503 `{"room":null,"reason":"LiveKit is not configured"}` | At least one of `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` is missing or blank on the service. A variable set to nothing but whitespace counts as missing here, because the reader trims before it tests, so a half-finished paste looks identical to an absent one. |
| 503 `{"room":null,"reason":"LiveKit call failed: ..."}` | The variables are set and LiveKit refused them or could not be reached. The tail carries the cause: `invalid API key` (observed) is a key this project does not know. A well-formed key and secret that do not match each other surface instead as `LiveKit rejected the API key and secret for this project`. |

**A healthy idle deployment answers 503, and that is not a defect.** It is worth
saying plainly because the instinct is to read any 5xx as broken and start
changing variables. The route is honest rather than reassuring: it will not mint
a ticket to a call that does not exist. If you want a 200 out of this endpoint,
start a call.

There is one way to get a misleading 200, and it is worth knowing before you
trust one. `LIVEKIT_ROOM` is unset on Railway on purpose, per the contract
above. **If it is set, the route stops calling LiveKit at all**: it skips the
room lookup and mints the token locally, and token minting is offline signing.
Measured, with a deliberately fake key and secret and `LIVEKIT_ROOM` pinned: the
route answers **200 with a fully-formed signed token**. Nothing validates those
credentials until a browser tries to use the token and is refused by LiveKit. So
a 200 with the room pinned proves that the service is running, and nothing
whatsoever about whether the secrets you pasted are correct. Leave `LIVEKIT_ROOM`
unset and the 503 reason does that job properly.

### `api/session/stream`: 503 is the finished state

```
curl -i https://<your-railway-domain>/api/session/stream
```

Expect 503 `{"live":false,"reason":"bridge not configured"}`, and stop there.
This is not a fault and there is no variable that fixes it: the event bridge is
same-host by construction, which is issue #63. `AMBASSADOR_BRIDGE_HANDSHAKE` and
`AMBASSADOR_AGENT_DIR` are deliberately unset on this service, the contract above
says why, and the surface degrades to replay exactly as intended. A deployer who
sets them chasing this 503 gets a container pointing at a directory with no
Python in it and a handshake file the other container owns.

### The whole check, in order

1. `railway logs -s agent-worker -d` and find `registered worker`. No line means
   not deployed, whatever the status says. If the deploy is failed or crashed,
   pass the deployment id or the default view will show you the last good one
   instead; stderr there names the missing variable. If it is green and the line
   is absent, look for the retry warnings and the drain.
2. `GET /` on the web domain returns 200. The image is layered right.
3. `GET /api/session/room` and read the reason. `no LiveKit room is open` or a
   200 are both passes; `is not configured` or `call failed` are the two real
   failures, and they tell you which.
4. `GET /api/session/stream` returns 503 `bridge not configured`. Expected.
   Leave it.

Anything beyond this is a live call, which is `task-railway-live-smoke` and
`docs/07-demo-runbook.md`, not deploy verification.

**Provenance, since a verification procedure is worth only what it was checked
against.** Steps 2, 3 and 4 were run against a production `web` build from this
tree: the 200 on `/` with no variables set, the `is not configured`, `invalid
API key` and `bridge not configured` reasons, and the misleading pinned-room 200
are all observed responses rather than readings of the source. Two lines here
could not be provoked and are marked as such: the `no LiveKit room is open`
pass needs valid credentials and an empty project, and is instead the documented
behaviour of `lib/livekit/room.ts` asserted in `web/tests/room-grant.test.ts`;
the `rejected the API key and secret` wording is read from that same route's
error handling.

On the worker side, both failure shapes were run in the image built from the
root `Dockerfile` at this commit: the empty-environment exit 1 with its five
names, and the junk-credential run with its sixteen retries, `unregistered`
drain and exit 0 with the timing taken from the container's own start and finish
times. Three things there are not mine. The `registered worker` line and its
JSON shape come from the pinned framework source, since a real registration
needs real credentials. The pre-#66 exit 0 on *missing* credentials was measured
at the #64 gate. And the claim that a wrong key against a reachable LiveKit host
ends the same way is read from `worker.py` - one `except Exception` around the
connect and the register handshake, on the same retry counter - not provoked,
because provoking it needs a real project to be refused by.

One boundary on the whole section: none of it has been run on Railway. Every
response and exit code above came from the two images built out of this tree,
and the platform's half - that a non-zero exit crash-loops and ends as a failed
deploy, and what `railway logs` shows by default - is the documented behaviour
of the policy in `railway.json` and of the CLI's own `--help`. Confirming it on
the real service is `task-railway-live-smoke`.

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
