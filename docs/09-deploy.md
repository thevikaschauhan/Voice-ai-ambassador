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

The service's config is `.railway/railway.ts` (the section below says why it is one file rather than a `railway.json` per service), and its `watchPatterns` cover `agent/**`, `data/**`, the `Dockerfile` and `.dockerignore` - without them a web-only push rebuilds and redeploys the worker, which drains live calls for a change it does not contain. `data/**` appears in BOTH services' patterns on purpose: an inventory edit is a deploy for the worker as well as the web surface, because the worker reads the same files. It sets no `startCommand`: the image's `CMD` carries `--drain-timeout 600`, and a start command specified there would silently replace it and take the drain with it.

That drain needed a second number to mean anything, and this is where it was missing. `--drain-timeout 600` is how long the *worker* will wait to finish a call; `deploy.drainingSeconds` is how long *Railway* waits between SIGTERM and SIGKILL, and its default is **0**. So the worker was asking for ten minutes to hang up gracefully and being killed on the spot. The config sets `drainingSeconds: 600` to match the CMD, and the two numbers are deliberately the same so neither can drift into being the real one. Railway's docs give no maximum for it, only that default of 0, so 600 is matched to our own timeout rather than to a documented ceiling.

Two of the worker's inputs are now per CALL rather than per service, and both are read in `entrypoint`. The language comes from the room's metadata, which the web talk route writes and this reads as the JSON string `{"v":1,"language":"en"|"ar"|"hi"}` - the codes are `Language` in `ambassador/schemas.py`, unknown keys are ignored so either side can add a field without a coordinated deploy, and a `v` that is present and not 1 is refused because `language` may not mean the same thing in a contract this build has not seen. An absent `v` is read as 1, since an absent version cannot be a future one. It is taken from `ctx.job.room.metadata`, the job's own room message, and not from `ctx.room`, which has no metadata until `ctx.connect()` has run and the entrypoint connects last, after STT, TTS and the prompt have been built from the language. Every failure - no metadata, unparseable, an unknown code, an unknown version - falls back to `LANGUAGE` and emits `language_selected` naming which failure it was, because an unattended visitor learns nothing from a call that refuses to start and we learn nothing from a fallback that does not say why. `ALLOW_UNCERTIFIED_LANGUAGE` is untouched by all of this: it gates what may be spoken at all, and a routing decision does not get to overrule a certification. The second per-call input is `DEMO_MAX_CALL_SECONDS`, the duration cap: zero disables it and zero is the default, so the laptop demo and the console are unchanged, and the hosted service sets a number because its URL is public and an abandoned open tab bills three metered providers. It is a cancelled sleeper that calls `ctx.shutdown()`, cancelled by the same shutdown callback that seals the audit, and it emits `call_duration_cap_armed` when it is set and `call_duration_cap` before it fires - the armed event exists so a cap that never took effect because its variable never reached the container is distinguishable from a call that simply ended early. An unreadable value refuses to start rather than being read as absent, because absent means uncapped.

### `web`

The browser gets a listen-only ticket to the call in progress, and the route that mints it is `web/src/app/api/session/room/route.ts`. What crosses the wire is the signalling URL, a room name, and a token that expires in ten minutes and can do exactly one thing: subscribe to audio in one named room. The API secret that signs it stays on the server.

This is why there is no separate token service: minting is one Next server route using `livekit-server-sdk`, which is the same tier the rest of the app's server work already happens in. It also satisfies the hard rule in AGENTS.md that no provider is ever called from the browser.

A `web` deployed without LiveKit variables does not crash. `api/session/room` answers 503 with an honest reason and the surface keeps its "no audio track" label, which is the correct behaviour for a machine that has no call to show.

## The configuration is one file, and the CLI applies it

Both services are described by `.railway/railway.ts`: sources, builders,
dockerfile paths, watch patterns, health check, restart policies, replicas, and
the variable names each service carries. One file for the project, not one per
service.

**It is not a `railway.json` because Railway retired that.** Config as code is
deprecated, and not gently: the API refuses to set a service's config file path
at all, answering `Config as Code (railway.json / railway.toml) is deprecated.
Use Infrastructure as Code (.railway/railway.ts) instead`. New services cannot
opt into it, existing files stop being read on **2026-12-01**, and the CLI
prints the deprecation warning on every command while one is still in the tree.

This project had two of those files and neither was ever read. Both services
were created after the change, and their live settings showed builder
`RAILPACK`, no watch patterns and no health check, while the two `railway.json`
files in the repository claimed `DOCKERFILE`, patterns and a health check on
`/`. The worker's image built from the Dockerfile anyway, but only because
Railway auto-detects a root `Dockerfile`, not because anything read the file
sitting next to it. Deleting both changed nothing: the plan was identical before
and after. Configuration that looks authoritative and is inert is worse than
none, because it is what you check when something breaks.

That paragraph is measurement, not inference: the refused mutation and both
services' live settings were read at provisioning, and the two plans, before and
after the files were deleted, were run from this tree.

The workflow is two commands, and the first one is safe:

```
railway config plan     # reads Railway, prints the diff, changes nothing
railway config apply    # shows the same plan, then asks before writing
```

Both walk up from the working directory to find `.railway/railway.ts`, so
either runs from the repository root. `plan` redacts variable values by default.

**After every `apply`, re-check that both services still have a deployment
trigger** (step 0 of the verification section says how). The worker's trigger
disappeared inside the window of two applies on 2026-09-02 and nothing reported
it; whether the engine removed it is unproven, and until it is known this check
costs one query and the alternative is a service that silently stops deploying.

Two things about the file are easy to trip over. **Omit means delete**: it
describes the whole environment, so removing a service or a variable name from
it is a request to remove that thing from Railway, and `apply` marks those as
destructive before it asks. And **the CLI needs the SDK**: `railway config plan`
refuses to run without it (`The Railway TypeScript SDK is not installed`), which
is the only reason there is a `package.json` at the repository root. It is not
the web app, which has its own in `web/`, and neither image installs from it.

**To prove a change is a no-op, declare the opposite and watch the plan speak.**
A clean plan on its own does not distinguish "this field matches" from "the plan
never looks at this field", which is the same broken-versus-nothing-to-do trap
step 0 was fixed for. Declaring `checkSuites: false` on `web` was removed on
exactly this basis: the plan read "already up to date" both with the line and
without it, so the removal was verified by declaring the opposite value instead,
which made the plan answer:

```
Plan: 0 to add, 1 to change, 0 to destroy
  ~ Update web source.checkSuites
    └ source.checkSuites (null → true)
```

That output proves two things at once: the field IS diffed, so the clean plan
means something, and the stored value is `null` rather than `false`, so Railway
was holding it unset and the declaration had been matching an absence. Railway
does not store a setting equal to its default, so declaring one either does
nothing or guarantees a permanently dirty plan. The probe is read-only and costs
one command.

### Provisioning, in three steps

1. Connect the repository to each service, with **Root Directory empty**. Not
   `/web` for the web service: the reason is in the `web` contract below, and it
   has already failed a build once.
2. Paste the secrets as service variables. The names are in
   `agent/.env.example`, plus the `web` additions in the next section;
   `.railway/railway.ts` lists them as `preserve()`, which means "keep whatever
   is set on Railway", so applying the config never writes or overwrites a
   value.
3. `railway config apply` from the repository root, and read the plan it prints
   before confirming.

Then find out whether it worked: "Verifying a deploy", below, is how.

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
verified against a running container, not read off the source. The two `DEMO_`
rows no longer carry an exception: they exist, and the behaviour each one
describes was exercised in the image before this sentence changed.

| Variable | Railway value | If absent |
|---|---|---|
| `LIVEKIT_URL` | service variable | `api/session/room` answers 503 with a reason; the surface keeps its "no audio track" label |
| `LIVEKIT_API_KEY` | service variable | same |
| `LIVEKIT_API_SECRET` | service variable | same |
| `LIVEKIT_ROOM` | unset | the server picks a room name, which is the intended behaviour. It must stay unset once the talk path ships: pinning one room name would send every client into the same call |
| `AMBASSADOR_AGENT_DIR` | **unset** | text mode has no agent to spawn, so on the hosted service it refuses with a reason and the page says so. A laptop with no agent still gets the labelled replay: both conditions are required (issue #63) |
| `AMBASSADOR_BRIDGE_HANDSHAKE` | **unset** | `api/session/stream` answers 503 `{"live":false,"reason":"bridge not configured"}`. Stays unset on the hosted service by decision, not by omission: the hosted transcript comes from `lk.transcription` instead (issue #63) |
| `DEMO_ACCESS_CODE` | service variable, never `NEXT_PUBLIC_` | `api/talk` answers 403 to every attempt, with the same words for a wrong code and an unset one - an unset gate is a closed gate. Its presence is also what tells the service it is the hosted one, which is what closes `api/session/room` and text mode |
| `DEMO_LANGUAGES` | `en`, until the Arabic and Hindi packets come back | the picker offers all three, which is what a laptop wants. Set to a comma-separated list to narrow it; a value that is set but names no known language falls back to **English only**, not to all three, because somebody who typed the variable meant to restrict and honouring a typo by re-opening two unauthored languages on a public URL is the opposite of what they asked |
| `DEMO_MAX_ROOMS` | service variable | the cap falls back to 2, its in-code default, rather than to unlimited. A value that is not a positive integer falls back the same way rather than reading as no limit |
| `ADMIN_ACCESS_CODE` | service variable, never `NEXT_PUBLIC_` | `/admin` has nothing to sign in to and says so; `api/admin/login` answers 403 to every attempt. Unset is closed, and separate from `DEMO_ACCESS_CODE` because that one is read out to a client while this one opens the lead database |
| `ADMIN_SESSION_SECRET` | service variable | a correct access code cannot mint a session, so login answers 503 rather than issuing an unsigned one. Deliberately not the access code and not the upstream token: three secrets doing one job each |
| `ADMIN_API_TOKEN` | service variable | the proxy routes answer 503 with an operator-facing log line. Read in exactly one module (`lib/admin/upstream.ts`), asserted structurally |
| `ADMIN_API_URL` | the admin API's private network address | same 503. The browser never sees or chooses it: the proxy table is fixed, so this cannot become an open relay |
| `PORT` | injected by Railway | falls back to 3000, set in the image |
| `NODE_ENV` | `production`, set in the image | the npm scripts pin it anyway. That pin was load-bearing under Next 15; under Next 16 it changes nothing measurable in the build, and it is kept for one command everywhere |

The three LiveKit variables carry the same names the agent uses, so they are the
`agent/.env.example` entries by reference and are not re-listed with values.

**This table is also the whole list, and it did not start that way.** The
service was provisioned with the agent's entire environment, so a public-facing
Node container held `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY` - two metered
provider credentials with no code path in `web` that can reach them - along
with seven model and mode values it never reads. Nine names were removed from
the service, and `.railway/railway.ts` lists only what remains, because a
credential that cannot be reached from a container cannot leak from one. The
`LIVEKIT_ROOM` and `AMBASSADOR_*` rows above are absent from that file for a
different reason: they are meant to stay unset, and naming them in the config
would be the first step towards someone setting them.

**There are no `NEXT_PUBLIC_` variables, and that absence is the security
property rather than an omission.** Nothing the browser needs is a secret: it
receives a signalling URL, a room name and a short-lived token minted in the
server route, listen-only for the viewer surface and publish-capable for the
talk path. A token is not a credential in the sense that matters here: it is
minted per call, scoped to one room, and expires. The API key and secret that
sign it never leave the server. A `NEXT_PUBLIC_` variable is compiled into the client
bundle, so introducing one to "make the URL available" would be the first step
of putting credentials in a page.

The two `AMBASSADOR_` variables must be left unset deliberately, not merely left
blank by oversight. Both are same-host paths (issue #63): setting `AMBASSADOR_AGENT_DIR`
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

- **Root Directory must stay empty.** Railway pulls only the files under a
  service's root directory, so setting it to `/web` removes `data/` from the
  build context and produces exactly the broken image above. This is not a
  hypothetical: the service was created with `/web`, and the first build after
  that failed with `"/data": not found`. `.railway/railway.ts` now declares
  `rootDirectory: null` so the setting is owned by code rather than by whoever
  clicked last.
- **The dockerfile path has to be given**, as `web/Dockerfile`. With the root
  empty, Railway's auto-detection finds the *worker's* `Dockerfile` at the top
  of the repository, which is the wrong image for this service.

The config sets no `startCommand`: the image's `CMD` is `npm run start`, so the
start path is defined once, in the file that also pins `NODE_ENV`.
`healthcheckPath` is `/` rather than a dedicated endpoint because `/` is the
page that reads `../data`, so a mislayered image fails its health check instead
of serving broken prices. `watchPatterns` covers `web/**` and `data/**`: an
inventory edit is a deploy for this service.

### The talk path, as built

`api/talk` is a POST, because it creates a metered resource and a GET that mints
one is a prefetch away from a bill. The order of its checks is the security
order rather than the convenient one: the access code is compared before
anything reaches LiveKit, so a refused attempt costs one string comparison
instead of a `listRooms` round trip.

`mintTalkGrant` sits beside `mintViewerGrant` rather than inside it. The viewer
grant withholds exactly the capability the talk path needs, so a single function
with a `canPublish` argument would be one wrong call away from handing a publish
token to the read-only surface. Both grants are decoded and inspected in tests,
which is what makes the pair a guarantee rather than an intention.

Every room is created here, per call, named `demo-<uuid>`, and the token is
scoped to that name. That is what makes "no listen-only token for a room the
requester did not create" enforceable rather than aspirational: there is no
lookup to get wrong, because the room did not exist until the call that asked
for it. `api/session/room` is therefore **closed on the hosted service** - its
newest-occupied-room lookup is right for a laptop watching its own agent and
would hand a stranger's conversation to whoever asked next. The laptop path is
untouched.

The cap counts only rooms whose names carry the `demo-` prefix, so a room some
other tool created in the same LiveKit project cannot lock the demo out. It is
counted before the room is created, and two visitors arriving in the same
instant can both pass the check: an accepted race whose cost is one extra room,
against a lock this service has nowhere to keep.

**What the visitor looks at during a call is an orb, not a control panel.** The
page's job while a call is live is to make an audio conversation legible, and a
form with a Mute button does not do that: a visitor with nothing to look at
cannot tell a thinking pause from a broken demo. So once the call starts the
surface is a dark disc with a coloured corona, and the corona carries the state
- breathing slowly while it listens, blooming with the ambassador's own voice
while she speaks, a cooler and tighter figure while the visitor speaks, and a
slow drift while the turn is being thought about. The transcript sits under it
as subtitles rather than a log: the current utterance large and centred, the
line before it fading above.

The level that drives the bloom is measured with a Web Audio `AnalyserNode` on
the attached tracks rather than read from `Participant.audioLevel`. Two reasons,
and the second is the one that matters. `audioLevel` updates on the order of
once a second, which is a meter reading rather than something speech can drive.
And `room-signals.ts` already established the rule for this repository: one
number, both readings. The server's speaker detection and a local analyser
disagree, and a corona that blooms while the label says silent - or the reverse
- is worse than either.

**The ambassador has a name, and it comes from data.** `data/ambassadors.yaml`
maps a language to a name, English **Jane**, and the page reads it at request
time through the same repo-root read that serves the inventory. An empty or
missing name falls back to "Binghatti's AI ambassador" rather than to a blank
label - the same posture as every other absent value on this surface. The name
labels the orb and prefixes her subtitle lines, so a visitor knows who is
speaking without being told out loud.

**`prefers-reduced-motion` is answered, not ignored.** The disc keeps its
corona and loses its movement: state is carried by colour and by the label
underneath, so the page says the same things without animating them.

**How a call ends is read from `DisconnectReason`, not inferred.** The agent
finishing politely and the network dying look identical inside
`ConnectionState`, and naming the wrong one is a lie in either direction: "the
ambassador ended the call" after a dropped signal, or "connection lost" after a
farewell the visitor just heard. So the reason is read. `ROOM_DELETED` (what a
worker shutting its job down produces), `ROOM_CLOSED` (the room's own timeout)
and `PARTICIPANT_REMOVED` (the shape a duration cap takes) are deliberate ends;
`DUPLICATE_IDENTITY` says another tab took the call; everything else, an absent
reason included, falls to "stopped unexpectedly". That default is the safe one -
it is true whatever happened and it offers another call, where claiming a
finished conversation would be a fabricated farewell.

The ending is also where the session tears itself down, rather than in the End
call button: the common case is the other side hanging up, so the transcript
handler is unregistered, the audio sinks are detached and the microphone is
released there, once, whoever ended it. The visitor's own End call routes
through the same path so the two cannot drift. The farewell stays on screen, and
the button becomes "Start another call" - which posts to `api/talk` again, so
the access code and the room cap are re-checked server-side even though the
code is still in the field.

Reconnect attempts are bounded to four over about four seconds. The library
default climbs through ten attempts to its maximum delay, and on a room that no
longer exists every one of them fails while the visitor reads "Reconnecting" for
the whole climb - the call was over at the first attempt.

The transcript rail reads the framework's `lk.transcription` streams, and the
two sides of the conversation arrive differently - which is a fact about the
framework, not a preference. `room_io.py` builds the USER output with
`is_delta_stream=False` and the AGENT output with `True`, and the non-delta
branch of `_ParticipantStreamTranscriptionOutput` "always create a new writer"
per update, writing the whole text so far and closing it. So the agent's segment
is one stream of deltas to append, while the visitor's is a succession of whole
texts in separate streams. The rail is therefore keyed on the `lk.segment_id`
attribute rather than on the stream id: keyed on the stream, one visitor
sentence piles up as several lines each a little longer than the last. The final
flag is `lk.transcription_final` and its value is the string `"true"`, read
rather than inferred from a stream ending, because on the visitor's side every
interim stream ends too. Measured on the agent side by execution
(`task-hosted-language-from-metadata`) and read from the framework source for
the visitor side; the visitor half is what the next hosted call settles. `TextStreamReader`'s own docstring says an async iteration returns
the whole string received so far, which reads as cumulative; the implementation
decodes and yields each chunk's own content, and `readAll` is what concatenates.
That was settled by reading `livekit-client`'s source, because appending
cumulative chunks would print every word an increasing number of times.

### The trap in `next start`, and how Next 16 closed it

Under Next 15, `next start` re-read `next.config.ts` at boot, and a TypeScript
config needs a TypeScript compiler to read. With production-only dependencies
installed Next did not fail with a missing module - it tried to **install
TypeScript at boot, with yarn, over the network, into the running container**, at
a version this repo does not pin. It needed egress at start-up, it wrote into
the image at runtime, and as a non-root user it failed outright. The image
shipped the pinned compiler to close it.

Next 16 resolves the config at **build** time and bakes it into
`.next/required-server-files.json` - that file carries this project's
`outputFileTracingRoot` of `/srv/` - so `next start` never parses the TypeScript
again. Checked by deleting `node_modules/typescript` from the image and starting
it: ready in 215ms, every route 200, and no install attempted in the boot log.
The compiler is therefore gone from the runtime tree, taking 23MB and a comment
that had stopped being true with it.

The config *file* still ships. It is 300 bytes and it is the declared source of
truth for `outputFileTracingRoot`; dropping it would make the image depend on a
build-artifact detail rather than on the file the repository maintains.

The same re-check settled the other half. The `NODE_ENV` pin in the npm scripts
was load-bearing under Next 15, where an inherited `NODE_ENV=development` made
the build prerender `/404` from the dev pages-router error component and fail on
`<Html>`. Under Next 16 the fourteen client chunks from a
`NODE_ENV=development` build are byte-identical to the pinned one. The pin stays
regardless: it costs nothing, `next start`'s runtime behaviour under a
development `NODE_ENV` was not measured, and one build command everywhere is
worth more than two saved lines.

## Build reproducibility

Both images should mirror `.github/workflows/gates.yml` rather than inventing their own toolchain, so that a green pull request means something about the thing that gets deployed.

For `agent-worker` that means uv 0.10.7, Python 3.14, and `uv sync --frozen`. The `--frozen` is not a style preference: `agent/pyproject.toml` sets `exclude-newer = "5 days"`, which is a *moving* window, so an unfrozen build resolves a different dependency set depending on the day it runs. A deployment that cannot be reproduced next week is not a deployment, it is a snapshot.

For `web` it means Node 24 and `npm ci` from `package-lock.json`, in the image
and in CI both. When the image was written there was nothing to mirror - the
four web gates were not in `gates.yml`, so no job pinned a Node version and the
image had to choose one. They are a third job in `gates.yml` now, and it pins
the same major the image does: both pin the major and float the patch, so they
move together rather than drifting apart one patch at a time. The rule that got
written down when only one side existed still holds - the Node version belongs
in both places or in neither.

`--ignore-scripts` is on in both, for the same reason: the only package in this
tree with a meaningful install script is `sharp`, for `next/image`, and this app
has no `next/image`. If an `<Image>` ever lands, both sides drop the flag
together and `sharp` gets pinned explicitly rather than half-installed.
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

- **The record is JSON, but what you see depends on the flag.**
  `setup_logging` installs a `JsonFormatter` whenever devmode is off, and the
  container's `CMD` runs the `start` subcommand, so the emitted record is a JSON
  object with `id` as a field rather than the `id=...` suffix a local dev run
  prints. `railway logs -d --json` shows you that. Plain `railway logs -d`
  flattens it back to `key="value"` text, so `id="AW_..."` does appear there
  (#74). Either way, grep for `registered worker`: it is the one token that is
  present in every rendering, and it is the thing you actually need to find.
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
settings a worker must choose explicitly: STT_ENABLED
  STT_ENABLED: set STT_ENABLED=true for the voice path (ADR-017), or =false to run deaf on purpose. It defaults to false, and with it off the recogniser's key is never asked for, so a worker starts happily and hears nothing.
Set them in agent/.env (see agent/.env.example) or in the environment.
```

Both kinds of problem are reported together, on purpose: a cycle here costs a
rebuild and a deploy, so learning about the second after fixing the first costs
a round trip for nothing.

A non-zero exit is what the restart policy is for, so the deploy crash-loops
through its ten retries and ends up **failed** on the dashboard, with the
variable names in the log. That policy is on-failure with ten restarts, and it
is Railway's own default rather than something this repository sets:
`.railway/railway.ts` deliberately does not declare it, because the platform
does not store a setting equal to its default and a declared default shows as a
pending change on every plan forever. The file says so in a comment where the
setting would have been.

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
worker can hear - a wrong Deepgram key or a provider outage still registers
fine. What it now does rule out is being deaf by OMISSION. `STT_ENABLED`
defaults to off, and with it off preflight does not ask for `DEEPGRAM_API_KEY`
at all, which is why that name is absent from the credentials line above; a
worker with all six secrets set therefore used to register, pass every check
the platform could see, and hear nothing. So preflight requires the variable to
be CHOSEN on the connecting subcommands - `true`, or `false` to run deaf on
purpose - and refuses to start when it is unset, blank, or misspelled, since
each of those is equally deaf and equally accidental. Measured in this image:
all six secrets and no `STT_ENABLED` exits 1 naming it; the same six with
`STT_ENABLED=false` reaches `registered worker` normally.

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
| 403 `{"room":null,"reason":"the listening view is not available on the hosted demo; start a call instead"}` | **This is the pass on the hosted service**, and it is the first row because it is the only one a deployed `web` with `DEMO_ACCESS_CODE` set will ever give you. The listening view finds its room by asking LiveKit for the newest occupied one, which is right for a laptop watching its own agent and wrong on a service where rooms are per-visitor. The rows below are the laptop's answers, and you will see them only with the access code unset. |
| 503 `{"room":null,"reason":"no LiveKit room is open; the agent is not in a call"}` | **This is the pass on a laptop**, with the access code unset. All three variables are set and LiveKit accepted them; there is simply no call in progress yet. You will not see it on the hosted service, and that costs the check something - see "What this check cannot prove" below. |
| 200 `{"url":...,"token":...,"room":...}` | Also healthy, and a call is live right now. The token is listen-only and expires in ten minutes. |
| 503 `{"room":null,"reason":"LiveKit is not configured"}` | At least one of `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` is missing or blank on the service. A variable set to nothing but whitespace counts as missing here, because the reader trims before it tests, so a half-finished paste looks identical to an absent one. |
| 503 `{"room":null,"reason":"LiveKit call failed: ..."}` | The variables are set and LiveKit refused them or could not be reached. The tail carries the cause: `invalid API key` (observed) is a key this project does not know. A well-formed key and secret that do not match each other surface instead as `LiveKit rejected the API key and secret for this project`. |

**A healthy idle deployment answers 403 hosted, or 503 on a laptop, and neither
is a defect.** It is worth saying plainly because the instinct is to read any
4xx or 5xx as broken and start changing variables. The route is honest rather
than reassuring: on a laptop it will not mint a ticket to a call that does not
exist, and on the hosted service it will not mint one at all. If you want a 200
out of this endpoint, start a call on a laptop.

There is one way to get a misleading 200, and it is worth knowing before you
trust one. It is a laptop-mode trap now rather than a hosted one, since the
hosted 403 lands before any of this, but it is the reason `LIVEKIT_ROOM` stays
unset on Railway too. `LIVEKIT_ROOM` is unset on purpose, per the contract
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

0. **Confirm each service is running the commit it ought to be running**,
   before asking whether it is healthy. This is the only step here that catches
   a deploy which never happened, and every step below will pass without it,
   because they all test the deployment that IS running.

   What each service has deployed:

   ```
   railway status --json | jq -r '.environments.edges[].node.serviceInstances.edges[].node
     | "\(.serviceName)  \(.activeDeployments[0].meta.commitHash[0:12])"'
   ```

   **Not against the head of `main`, though.** A service only deploys when a
   push touches its own `watchPatterns`, so one that has nothing to build sits
   behind `main` and is perfectly healthy. Its expected commit is the newest
   commit on `main` that touched its patterns:

   ```
   for spec in "Voice-ai-ambassador:^(agent/|data/|Dockerfile$|\.dockerignore$)" \
               "admin-api:^(agent/|data/|Dockerfile$|\.dockerignore$)" \
               "web:^(web/|data/)"; do
     name=${spec%%:*}; pattern=${spec#*:}
     expected=$(git rev-list -50 origin/main | while read -r c; do
       git show --name-only --format= "$c" | grep -Eq "$pattern" && echo "$c" && break
     done)
     printf '%-22s expects %s\n' "$name" "${expected:0:12}"
   done
   ```

   Compare the two lists per service. A service on its expected commit is
   current, whatever `main` says. **Only a service behind its OWN expected
   commit has missed a deploy**, and that is the one case worth investigating.

   The pattern sets in that command are copies of the `watchPatterns` in
   `.railway/railway.ts`, and copies drift: if that file's patterns change, this
   command changes with them, or it starts quietly expecting the wrong commit.
   It has drifted once already - the list held two services for a day after
   `admin-api` became the third, so the new service was the one thing the step
   could not report on. `admin-api` and the worker share a pattern set because
   they share the image.

   Two readings of this step are wrong in opposite directions. A service AHEAD
   of its expected commit has not failed: setting a variable redeploys a service
   at whatever `main` then points to, so a provisioning pass leaves every
   service it touched sitting on a commit that changed nothing they build. And
   for `admin-api`, a deployment reading `SUCCESS` is not a running process -
   its start command can fail on loop while the deployment stays green, so read
   its logs and not its status.

   The distinction matters more than it looks, and it is worth naming once
   because this document has three instances of it. A signal that cannot tell
   **broken** from **nothing to do** is not a check, because the false alarms
   teach people to stop reading it - which is exactly how the outage recorded in
   the provenance below went unnoticed for at least fifteen hours. The other two instances: a
   `mergeable: CONFLICTING` pull request may merely be behind its base rather
   than in conflict (`git merge-tree --write-tree main <branch>` separates
   them), and `source: {"repo": "..."}` below reads the same on a service that
   deploys and one that cannot.

   When a service IS behind its own expected commit, check that it still has a
   **deployment trigger**. That is not the same thing as being connected to the
   repository, and the confusion is the trap: `railway status --json` reports
   `source: {"repo": "..."}` identically on a service whose pushes deploy and
   one whose pushes go nowhere. The trigger is what turns a push into a build.

   What answers it is the COVERAGE of the deployment list. A service whose
   trigger works has one record for every push to `main`, not only for the
   pushes that built something:

   ```
   svc=Voice-ai-ambassador          # then again for web
   railway deployment list -s "$svc" --limit 100 --json \
     | jq -r '.[] | "\(.meta.commitHash[0:9]) \(.status)"' > /tmp/records
   base=$(tail -1 /tmp/records | cut -d' ' -f1)
   for c in $(git rev-list --reverse "$base..origin/main"); do
     s=$(git rev-parse --short=9 "$c")
     r=$(grep -m1 "^$s " /tmp/records) && echo "  $s  ${r#* }" \
                                       || echo "  $s  NO RECORD"
   done
   ```

   It walks back only as far as the oldest row the list returned, because a
   list truncated by `--limit` reports every older push as missing, and a
   check that invents its own failure is worse than no check.

   Three readings, and only the third is a fault:

   - `SUCCESS`, `FAILED`, `CRASHED`, `REMOVED`: the push matched this service's
     `watchPatterns` and was built.
   - `SKIPPED`: the push did not match, and the webhook recorded that it looked.
     This row is the only positive proof a webhook evaluated the push at all,
     because nothing creates a SKIPPED row by hand.
   - `NO RECORD`, where the rows around it are dense: nothing evaluated that
     push. No deploy, no failure, and no skip either.

   Two things the list cannot tell you, and both have already misled somebody
   on this project. `meta.reason` reads `"deploy"` on a hand-made deployment
   exactly as it does on a webhook one, so a recent successful deploy of the
   current commit is not evidence that the trigger lives - somebody may simply
   have clicked it. And the absence of SKIPPED rows proves nothing by itself,
   because it is equally what a healthy service looks like when every recent
   push happened to match its patterns. Only coverage across a window of
   pushes separates the two, which is why this reconciles pushes against rows
   instead of querying for a status.

   The lag corroborates. A webhook row appears two to five seconds after its
   push; a row sitting minutes behind its commit is usually a person, or a
   variable change redeploying whatever was HEAD at the time. That second kind
   is what masked the outage recorded in the provenance below, because it kept
   producing recent successful deploys while no push was being evaluated.

   If the list is inconclusive, the trigger is visible in the dashboard under
   the service's **Settings > Source**, or over the API, which answers directly
   and needs an admin-scoped token:

   ```
   deploymentTriggers(projectId: ..., environmentId: ..., serviceId: ...)
   ```

   An empty list there means no webhook evaluation happens at all. It is the
   quietest failure on this page.

1. `railway logs -s agent-worker -d` and find `registered worker`. No line means
   not deployed, whatever the status says. If the deploy is failed or crashed,
   pass the deployment id or the default view will show you the last good one
   instead; stderr there names the missing variable. If it is green and the line
   is absent, look for the retry warnings and the drain.
2. `GET /` on the web domain returns 200. The image is layered right.
3. `GET /api/session/room` and read the reason. On the hosted service the pass
   is 403 `the listening view is not available` - that route is closed there by
   design, and because it is closed this step no longer says anything about the
   `web` service's LiveKit credentials. On a laptop, with the access code unset,
   `no LiveKit room is open` or a 200 are both passes and `is not configured` or
   `call failed` are the two real failures - and there the step does prove those
   credentials, which is the difference between the two modes.
4. `GET /api/session/stream` returns 503 `bridge not configured`. Expected.
   Leave it.
5. `GET /talk` returns 200, then `POST /api/talk` with no body:

   ```
   curl -i -X POST https://<your-railway-domain>/api/talk \
     -H 'content-type: application/json' -d '{"language":"en"}'
   ```

   403 `that access code was not accepted` is the pass, and it is the same
   answer whether the code is wrong or unset - the difference is in the service
   log, which says `an unset gate is a closed gate` when nobody has set one.
   Getting a 200 out of this endpoint means minting a real room, so do it with
   the code once and then let the room's departure timeout reclaim it rather
   than leaving it against the cap.
6. `GET /text` shows `Unavailable` and says the page does not answer. That is
   the hosted refusal, not a broken page.

Anything beyond this is a live call, which is `task-railway-live-smoke` and
`docs/07-demo-runbook.md`, not deploy verification.

### What this check cannot prove

Say the limit out loud, because the section spends its length insisting that a
green tick is not evidence and would be a poor place to quietly overclaim.

On the hosted service these six steps prove two things and not a third. They
prove the **worker's** copy of the credentials, because `registered worker` only
appears after LiveKit accepted them (step 1). They prove the **web image's
layout**, because `/` cannot answer 200 without `data/` beside the app (step 2).
They do **not** prove the `web` service's `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET`. Every step above would pass exactly as it does now with
three junk values on that service.

The reason is #78, and it is a fix rather than a regression: `api/session/room`
used to reach LiveKit to find the room, which is what made its 503 the one
response that proved those three values were *correct* rather than merely
*present*. It is closed on the hosted service now, and the 403 lands before
`liveKitConfig()` is even read. Nothing else in the check calls LiveKit from the
web service. Worth being precise about the risk this leaves: the worker and the
`web` service hold **separate copies** of the same three variable names, pasted
separately, so one can be right while the other is wrong and every check above
still passes.

**The proof is the first real call on `/talk`, and it is owed after any change
to the `web` service's variables.** That call is the only thing that makes the
web service present its credentials to LiveKit. What a wrong trio looks like
there, read from `api/talk/route.ts`:

| Response | What it means |
|---|---|
| 503 `{"room":null,"reason":"this demo is not connected to LiveKit"}` | One of the three is missing or blank on the `web` service. `liveKitConfig()` trims before it tests, so a whitespace-only paste reads as absent. |
| 502 `{"room":null,"reason":"LiveKit rejected this project key and secret"}` | All three are set, LiveKit was reachable, and it refused them. This is the wrong-trio answer, and the one worth memorising. |
| 502 `{"room":null,"reason":"could not start a call just now"}` | Something else failed on the way, an unreachable host among them. The detail is in the service log, not the response. |
| 429 | The concurrency cap, not a fault: the service is fine and the visitor should come back. |

So the honest reading of a green six-step check is "this deployment is up and
correctly built, and the worker can reach LiveKit". Whether the *browser* can is
settled by one call, and until somebody makes it the web service's trio is
unverified. That is the trade the safety fix bought, and it is a good trade: a
verification step is not worth handing a stranger a token for someone else's
conversation.

**Provenance, since a verification procedure is worth only what it was checked
against.** Steps 2, 3 and 4 were run against a production `web` build from this
tree: the 200 on `/` with no variables set, the `is not configured`, `invalid
API key` and `bridge not configured` reasons, and the misleading pinned-room 200
are all observed responses rather than readings of the source.

**Step 3's `no LiveKit room is open` pass was observed, and then went out of
reach.** It was first written from `lib/livekit/room.ts` and the assertion in
`web/tests/room-grant.test.ts`, because provoking it needs valid credentials and
a project with no room open. It was then observed on the hosted deployment:
`GET /api/session/room` answered 503 `no LiveKit room is open; the agent is not
in a call` against deployment 53a92ef7. That mattered more than the wording
suggested, because the string is only reachable *after* `listRooms()` returned,
so it did not merely mean the three variables were set - it meant LiveKit had
accepted the key and the secret.

**The worker spent at least fifteen hours not deploying, and none of the steps
above said so.** Recorded because it is the episode that added step 0. The
worker stopped recording pushes no later than 2026-09-02 12:22Z, where the
first unrecorded push is `54636500e`, and the absence of the trigger was proven
at 17:41Z or later, when `deploymentTriggers` returned an empty list for it and
one trigger for `web`. It was recreated at 2026-09-03 03:51Z and the first
clean worker row is `7abf49c2` at 04:01Z. Merges touching `agent/**` produced
no deploy, no failed deploy and no skipped deploy, because with no trigger
there is no webhook evaluation to record anything - which is why querying for
SKIPPED deployments returned nothing and proved nothing. It was masked as well
as silent: variable changes in that window redeployed HEAD, so the service kept
showing recent successful deploys of code that happened to be current at the
time.

**Fifteen hours is a lower bound, not a duration.** It is what the deployment
list can still see: the walk stops at the oldest row the list returns, and the
worker's gap was already open there. The honest statement is "at least fifteen
hours, start unknown".

What is proven: the trigger was absent when it was queried, recreating it and
deploying `53d5170` worked, and every push since 04:01Z is recorded on both
services. What is NOT proven, and is now unlikely ever to be: what removed it.
The cause is unknown.

The two `railway config apply` runs at 17:44Z and 17:55Z were the prime suspect
for a day, and the coverage check retired them. The worker had already stopped
recording pushes at 12:22Z, more than five hours before the first of those
applies, so neither can have caused this outage. That clears them of THIS
episode and of nothing else. It also withdraws a claim this section used to
make: "the last push-triggered worker deploy was 17:41:45Z on `b3fcfd7`" cannot
be right, because the trigger was already not recording pushes five hours
earlier, so that row is one of the masking deploys rather than a triggered one.
Its 67-second lag was the tell, and `meta.reason` said `"deploy"` either way.

The re-check after every apply, in the configuration section above, stays. It
is insurance on a failure that took fifteen hours to notice and costs one
command, and it is explicitly NOT a causal claim about apply.

**#78 closed that route on the hosted service at 17:55Z**, for the reason in the
first row of step 3's table, and the observation above now describes laptop mode
only. It is left standing rather than deleted because it is still the laptop's
expectation and it is still the only response either mode has ever produced that
proved those credentials correct. What the hosted service lost with it is set out
in "What this check cannot prove" above; the 403 that replaced it was observed on
deployment bad71116 the same evening, along with the `/` 200 on the Next 16
build and the worker's registration on d0e1b65d.

One line here still could not be provoked and is marked as such: the `rejected
the API key and secret` wording is read from that route's error handling, since
producing it needs a well-formed key and secret that do not match each other.

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
of the restart policy (`docs/deployments/restart-policy`: "The default is `On
Failure` with a maximum of 10 restarts") and of the CLI's own `--help`.
Confirming it on the real service is `task-railway-live-smoke`.

## The hosted stack is a client-facing demo source

The open question in this section used to be whether the hosted stack is what
the meeting is demonstrated from or a persistent environment alongside a laptop
demo. It has been answered, and the answer is neither: **the web URL is shared
with the client so they can try the POC at their end, with nobody from us
present.** That is the demanding case. An unattended visitor cannot be handed a
caveat out loud, so anything the hosted surface cannot honestly do it has to say
for itself, on the page.

Two of the surface's features are same-host by construction, and separate
containers end both. Neither is a defect; both are deliberate designs that
assumed one machine. Tracked as **issue #63**, which is the citation to use from
a build pull request rather than this heading.

**Text mode.** `web/src/lib/textmode/process.ts` spawns `uv run python -m
adapter.textmode` as a child process in `AMBASSADOR_AGENT_DIR`. The `web`
container is a Node image: no Python, no uv, no copy of `agent/`. Worth being
precise about what happens today, because it is not a crash:
`api/text-turn/route.ts` picks `processTextCore(dir)` only when
`AMBASSADOR_AGENT_DIR` names a directory and `replayTextCore()` otherwise, and
the page says which one it got. So the hosted text page serves a labelled replay
rather than attempting a spawn.

**The live event stream.** The transcript, latency meter and guardrail decisions
arrive over the event bridge, and the bridge is loopback-only on purpose: the
agent binds nothing but localhost, the handshake is a `0600` file on a shared
filesystem, and `handshake.ts` refuses any host that is not loopback so a
rewritten handshake cannot point the token elsewhere. Two Railway services share
neither a filesystem nor a loopback interface. The restriction is a security
property, so the answer is not to relax it.

### What ships instead

**A browser talk path.** The client enters an access code, picks a language, and
talks to the ambassador. A server route creates a room and mints a token that
can publish, which is the one grant `mintViewerGrant` deliberately withholds
(`room.ts`: `canPublish: false`, `hidden: true`, ten-minute TTL, for a surface
that watches a call rather than joining one). The talk path needs its own route
and its own grant rather than a loosened viewer grant, so that the listen-only
token stays listen-only and the two intentions cannot be confused in review.

The browser publishing a microphone does not breach the rule that the UI makes
no provider calls. The microphone goes to LiveKit transport; recognition,
synthesis and inference all stay in the worker with the keys. The rule is about
where provider credentials live, and this moves none of them.

**Per-call language.** Today the language is a per-worker environment value
(`config.py`, `LANGUAGE`, default `en`), so a worker speaks one language for its
whole life. The room will carry its language in room metadata and the entrypoint
will read it, falling back to `LANGUAGE` when the metadata is absent or
unparseable. The metadata is the JSON string `{"v":1,"language":"en"|"ar"|"hi"}`,
unknown keys ignored, and the envelope version is read asymmetrically on purpose:
a `v` that is present and not 1 is REFUSED, because `language` may not mean the
same thing in a contract the reader has not seen, while an ABSENT `v` is read as
1, because an absent version cannot be a future one and refusing it would turn a
writer's omission into a wrong-language call. The `ALLOW_UNCERTIFIED_LANGUAGE`
rule is untouched: it is a certification gate on what may be spoken at all, not a
routing decision.

**The transcript, from the framework rather than the bridge.** The hosted
transcript rail reads the framework's own transcription text streams. What
reaches those streams is the buyer-visible text and only that, which is why this
substitution is honest rather than a downgrade dressed up.

**Text mode degrades honestly.** On the hosted service `/text` and
`api/text-turn` refuse with a reason and a sentence on the page. This is a
change from what happens today, and the change is the point: a labelled replay
is safe when a presenter is there to narrate the label, and misleading when a
client is typing their own questions into it and getting scripted answers back.
Refusing says less and implies nothing false. Text mode stays what it was built
to be, the laptop's fallback for a room with bad audio.

**The tech lead's panels stay laptop-only.** The latency meter, the guardrail
and violation panels and the ambassador brief carry unredacted records, which is
exactly what issue #30 keeps loopback-bound. They are the tech lead's screen in
the meeting, not the client's. The hosted page states in one sentence which
panels it is not showing, instead of replaying a fixture into them.

### Abuse controls, because the URL is public

A public URL in front of metered providers is a spend surface. The access code
is checked server-side and is never a `NEXT_PUBLIC_` variable, since a
`NEXT_PUBLIC_` value is compiled into the client bundle and an access code in
the bundle is decoration. A cap on concurrent demo rooms is enforced by listing
rooms before minting a token, reusing the call `activeRoom` already makes. Token
TTL stays short, and the room's own timeouts close rooms nobody joined and rooms
everybody left.

The per-call duration cap is **in scope**, on the test god's card set: it is a
timer in the entrypoint and nothing more. `ctx.shutdown(reason=...)` exists and
is synchronous, and the entrypoint already registers a shutdown callback that
seals the audit, so a cancelled sleeper that calls it needs no new machinery and
no new failure mode. Anything larger, such as metering minutes across calls or
per-visitor quotas, is out.

### What a builder should verify rather than take from this document

These were read out of the pinned dependencies in this repository, not from a
documentation site, so they describe the versions that actually ship
(`livekit-agents` 1.7.0, `livekit-server-sdk` 2.18.0). They are still worth
re-checking at build time, because a scope document is not a test.

| Claim | Where it was read | Why it matters here |
|---|---|---|
| An unset `agent_name` means automatic dispatch | `worker.py`: `agent_name: str = ""`, documented as "Set agent_name to enable explicit dispatch. When explicit dispatch is enabled, jobs will not be dispatched to rooms automatically" | `agent.py` constructs `WorkerOptions(entrypoint_fnc=..., prewarm_fnc=...)` with no `agent_name`, so a server-created room gets the agent with nothing dispatching it explicitly |
| The `WorkerOptions` path takes no `agent_name` from the environment | `worker.py`: the `LIVEKIT_AGENT_NAME` and `LIVEKIT_AGENT_NAME_OVERRIDE` fallbacks sit inside the `rtc_session` decorator, not on the `WorkerOptions` path | A stray `LIVEKIT_AGENT_NAME` service variable would otherwise silently switch the worker to explicit dispatch, and the symptom would be a room where the agent never arrives |
| Transcription streams are published by default | `room_io/types.py`: `transcription_enabled: NotGivenOr[bool] = NOT_GIVEN`, "If not given, default to True"; the topic is `lk.transcription` in `types.py` | `session.start(agent=agent, room=ctx.room)` passes no output options, so the hosted rail has a source without an agent-side change |
| Both sides of the conversation reach those streams | `room_io/room_io.py` builds a user transcription output and an agent transcription output in the same enabled branch | A rail fed by only one of them would show half a conversation |
| The streams carry real words, not TTS respellings | `agent.py`'s `tts_node` applies `respell_stream` inside itself, and its own comment says respelling happens there and nowhere earlier; the framework forks transcription from `transcription_node`, which this agent does not override | The client would otherwise read "bin-GAH-tee" on screen |
| The words are the verbalised form | `interception.py` yields `decision.spoken` | The rail reads as speech, so a figure appears as words rather than as digits. That is what the buyer heard, and it is the honest thing to show, but it is not the digit form a reader might expect |
| Room metadata is readable before `connect` | `ctx.job.room.metadata` is a `str` on the job's room message (`protocol/models.pyi`), where `ctx.room` is the connected `rtc.Room` | The entrypoint calls `ctx.connect()` last, after building STT, TTS and the LLM from settings. Reading the job's copy means the language is known in time and the entrypoint needs no reordering |
| `createRoom` takes metadata and both timeouts | `RoomServiceClient.d.ts`: `metadata?: string`, `emptyTimeout?: number`, `departureTimeout?: number`, `maxParticipants?: number` | Metadata is a string, so the language has to be serialised. `departureTimeout` is the one that reclaims a room after the client closes the tab, and without it the concurrency cap counts rooms nobody is in |

Two things could not be verified by reading, and are the live smoke's job
(`task-hosted-live-smoke`): that LiveKit Cloud dispatches this project's worker
to a room the web service created, and that a real browser's published
microphone reaches the worker and the agent's audio comes back.

### The three build cards this scope implies

- **`task-hosted-language-from-metadata`** (agent side): read the language from
  room metadata with a fallback to `LANGUAGE`, and verify the transcription
  streams carry what the table above says they carry.
- **`task-hosted-talk-page`** (web side): the talk page and its publish-capable
  token route, the access gate and the room cap, and `/text` refusing honestly.
- **`task-hosted-live-smoke`**: a real browser against the hosted worker, which
  is the only place the two unverifiable claims get settled.

Issue #63 stays open until those cards close it.

## Phase 2 hosted-demo database check

This section takes effect with P2-S13; the current Phase 1 deployment still has
no database. The Supabase project is created in `eu-central-1` Frankfurt and
both Python services receive only the dashboard-issued Supavisor **session**
mode URL on port 5432 as `DATABASE_URL`. The value moves directly from the
Supabase dashboard to Railway variables and is never pasted into a ticket,
commit, terminal transcript or hive message.

### Provisioning the Supabase project, in order

The first step is the one that cannot be undone, so it goes first deliberately.

1. **Create the project in `eu-central-1` (Frankfurt).** It is the closest
   offered region to the Railway services, which run in Amsterdam. There is no
   Amsterdam Supabase region. `VERIFY:` Supabase does not document whether a
   project's region can be changed afterwards, so this runbook treats creation
   as irreversible.
2. **Choose the Free plan**, knowing its three operational edges: 500 MB of
   database storage, a limit of two active projects, and the inactivity pause
   that the check below exists for. `VERIFY:` the pricing page states 500 MB
   while the compute-and-disk page mentions 8 GB in a disk-provisioning context
   and warns that free compute is "subject to change"; read the figure in the
   dashboard rather than from here.
3. **Take the session-pooler URL, not the direct one.** In the dashboard's
   **Connect** dialog choose Supavisor **session mode** on port **5432**. Not
   the direct connection and not transaction mode, for the reasons in
   `docs/10-admin.md`: the free direct endpoint is IPv6-only while Railway's
   outbound IPv6 is off per service, and transaction mode does not support
   prepared statements.

   **The dialog defaults to transaction mode on 6543, and the session URI is
   the same string with `5432` in place of `6543`.** So the wrong value is the
   one a copy-paste produces, and it differs from the right one by four
   characters. This instruction was already in this document, worded as the
   requirement above, and the port still went in as 6543 - naming the value to
   use is not enough when the value to avoid is the default sitting beside it.

   **`5432` alone does not identify the right URL, because two entries in that
   dialog carry it.** The direct connection is also on 5432, and on a free
   project its host is IPv6-only while Railway's outbound IPv6 is off per
   service, so it cannot connect at all. The discriminator is the HOST:

   | | user | host | port | reachable from Railway |
   |---|---|---|---|---|
   | **session pooler** | `postgres.<ref>` | `aws-<region>.pooler.supabase.com` | **5432** | yes, IPv4 |
   | transaction pooler | `postgres.<ref>` | `aws-<region>.pooler.supabase.com` | 6543 | yes, IPv4 |
   | direct connection | `postgres` | `db.<ref>.supabase.co` | **5432** | **no** - IPv6 only on Free |

   Read that table by columns rather than by rows, because it says which check
   catches which mistake, and **no single field catches both**:

   - the **user** separates pooler from direct - `postgres.<ref>` against a
     bare `postgres` - and it is the field a human cannot misread. For a
     human only, though: bare `postgres` is also the ordinary user on
     localhost and in CI, so code that refused it would fail the gates that
     protect the code. In a check that runs anywhere, the **host** shape is
     the unambiguous one;
   - the **port** separates session from transaction, and only that, since
     both pooler modes share one host;
   - the **host** confirms the first of those, and is the longest to eyeball
     and the only one safe to assert in code.

   So a correct URI passes three shape checks: user starts with `postgres.`,
   host contains `pooler.supabase.com`, port is 5432. `VERIFY:` these shapes
   are from Supabase's connection guide, read 2026-09-03; the dashboard is the
   authority if it disagrees.

   Which makes the rule not "use 5432" but **take the pooler URI you already
   have and change only its port**. Asking for "the port on 5432" produced the
   direct URI on the first attempt here, because the dialog offers one. Its
   signature in the `admin-api` pre-deploy log is:

   ```
   migrations failed: OSError: [Errno 101] Network is unreachable
   ```

   That is `ENETUNREACH`, which is what an IPv6-only host looks like from a
   service with IPv6 egress off. It is never a Supabase outage and never a
   password problem, and it is the one failure here that says nothing about
   credentials at all.

   Worse, the mistake survives the step that ought to catch it. Migrations
   **pass** on 6543: the runner opens one short-lived connection, so its
   `applied ... migration(s)` line says the URL reaches the database and says
   nothing about the port being right. The runtime pool is where it breaks -
   `adapter/repository.py` keeps `statement_cache_size` at its default, which
   transaction mode would require to be 0, and its own docstring says so. A
   green provisioning run is therefore compatible with a URL that every
   runtime query will fail against.
   **What a correct URL looks like in the logs.** Since `adapter/session_mode.py`,
   both Python services refuse a URL that cannot work, on the shared connect
   path that the migration runner and `Repository.connect` both call - so the
   refusal happens at `admin-api`'s pre-deploy and at the worker's first write,
   not only in one of them. Two refusals, quoted as shapes:

   ```
   DATABASE_URL is a Supabase direct-connection host, which is IPv6-only; use
   the session pooler (pooler.supabase.com) on port 5432.
   DATABASE_URL uses port 6543 (Supavisor transaction mode); this process needs
   session mode on port 5432.
   ```

   Neither names the host or the user, because a Supabase hostname carries the
   project ref and the URL carries a password. A refusal fails the deployment
   and leaves the previous version serving.

   Two lines confirm the good case, and **you need both**:

   ```
   database port 5432 (session mode)          # admin-api pre-deploy, before the migration lines
   lead_store_connected target=<host>:<port>  # the worker, on its first write
   ```

   The first shows the **port** and deliberately nothing else. The second shows
   the **host** with the port, credentials stripped at the `@`. That division
   is the table above restated: the port separates session from transaction and
   the host separates pooler from direct, so a log that shows only the port
   cannot distinguish the incident this section exists to describe. The guard
   refuses the direct host before either line prints, which is what makes the
   port line meaningful - but it is the worker's line that lets a reader SEE
   the pooler.

4. **Set the values as Railway service variables**, using the table below. They
   move from the Supabase dashboard into Railway directly. A connection string
   contains the database password, so it never goes into a commit, a ticket, a
   terminal transcript, a chat message or this file.

   **A variable write is a deploy.** Every service written to redeploys at
   whatever `main` then points to, so provisioning restarts the live voice
   worker: it drains for `drainingSeconds` (600) and re-registers with LiveKit
   about twenty seconds later. Provision when a dropped call is acceptable, and
   confirm the worker came back by its `registered worker` log line rather than
   by the deployment turning green.

   One of those values is not a secret and is fixed by the configuration rather
   than chosen: `ADMIN_API_URL` on `web` is
   `http://admin-api.railway.internal:8080`. The host is the private endpoint
   `.railway/railway.ts` declares for `admin-api`, the port is the one its start
   command binds, and `http` rather than `https` because private traffic is
   already encrypted by Railway. It is set by hand because Railway's reference
   variables can supply the bare host but cannot add a scheme and a port.
5. **Tell whoever runs `apply`** that the values are in place. The apply is a
   separate step from provisioning and comes after, because the variable names
   are already declared and `preserve()` never writes a value.

### Which variable goes on which service

`.railway/railway.ts` names every one of these as `preserve()`, so the file
protects them without ever holding a value. The placement is the security
boundary, not a convenience: it is why the browser cannot reach the database and
why the web tier cannot bypass the admin API's bearer check.

| variable | `web` | `agent-worker` | `admin-api` |
| --- | --- | --- | --- |
| `DATABASE_URL` | no | yes | yes |
| `PII_ENCRYPTION_KEY` | no | yes | yes |
| `PII_HASH_KEY` | no | yes | yes |
| `ADMIN_API_TOKEN` | yes | no | yes |
| `ADMIN_API_URL` | yes | no | no |
| `ADMIN_ACCESS_CODE` | yes | no | no |
| `ADMIN_SESSION_SECRET` | yes | no | no |

Three of those noes are the design rather than housekeeping. **`web` never gets
`DATABASE_URL`**: its server routes call the admin API's private address and add
the bearer server-side, so a mistake in the web tier cannot become a mistake
against the database. **`agent-worker` never gets `ADMIN_API_TOKEN`**, because it
does not call the admin API; it writes through the same repository adapter. And
**neither Python service gets `ADMIN_ACCESS_CODE` or `ADMIN_SESSION_SECRET`**,
which belong to the browser-facing gate alone.

### Verifying the three-service topology

`railway config plan` is the check, and it is the value-safe one: it redacts
variable values by default, whereas `railway variables` prints them. Never reach
for the latter to answer a question about which service carries a name.

Before the apply, the plan must show `admin-api` as a **create** with **nothing
destroyed** on `web` or `agent-worker`. Against the two-service graph that
preceded it, the same plan reads "already up to date" and adds no admin service
at all, which is what a missing topology looks like rather than a broken one.

After the apply, the plan must read:

```
✓ Your Railway configuration is already up to date.
```

That single line is the topology assertion, and it is stronger than it looks
**because omit means delete**. The file describes the whole environment, so a
clean plan means Railway carries exactly the three services and exactly the
variable names declared here - including `DATABASE_URL` present on both Python
services and absent from `web`. A variable added to the wrong service by hand
shows up as a change, and one removed from the file shows up as a destroy.

If you doubt the plan is reading the field you care about, use the positive
control from the configuration section above: declare the opposite and confirm
the plan answers.

The plan says nothing about the database itself, though, and `preserve()` is
why: it asserts a name and never a value, so a clean plan holds with a
connection string that points anywhere. The schema is confirmed from the
`admin-api` pre-deploy log instead, which prints one of two shapes:

```
applied <n> migration(s): <versions>   # whatever was outstanding
schema is up to date at version <v>    # nothing outstanding
```

`<v>` is the highest-numbered file under `agent/migrations/`, so read it from
the tree rather than from here: this document deliberately quotes no version
number, because a line that names today's version goes stale the next time
somebody adds a `.sql` file and no diff to this file will show it.

Either shape proves the service reached Postgres and the schema is at the
version this build expects. Neither proves the port is 5432 - see step 3.
A pre-deploy that instead exits non-zero with `DATABASE_URL is not set` has
done its job: the deployment fails, and the previous version keeps serving.

### The pre-demo check

The free project may pause after roughly one idle week. The admin API runs one
bounded `SELECT 1` each day as a keep-active mitigation, but the pre-demo check
is still mandatory:

1. Open Supabase Studio and verify the project is active. If it is paused, use
   the one-click restore action and wait for Studio to report active.
2. Call the bearer-protected admin `/ready` route from inside the web service.
   It must report a compatible schema; `/health` proves process liveness only.
3. Run one read-only lead-list request and one knowledge search through the
   fixed web proxy. Do not print response bodies or any variable value.
4. Confirm the worker has not emitted `lead_persist_failed` or a failed
   `knowledge_retrieval` since readiness recovered.

A failed check blocks the Phase 2 admin demo, not the voice agent. The worker
continues with its base inventory, finishes the authored farewell and emits a
classified failure without buyer words. Free-tier restore through Studio is
available for one year after a pause; a paid Supabase plan, which does not
inactivity-pause, is the production answer.

## Not deployed

One project, one environment, so there is no staging tier. No custom domain: the generated Railway domain is the demo URL. No autoscaling and no replica count above one; a single concurrent call is what the demo needs and a second worker replica would race for the same job. **In the currently deployed Phase 1**, there is no database (in-memory session state, `STUB:` on the CRM write); the Phase 2 destination is the section above. Web gates in CI are no longer absent: `npm test`, `npm run typecheck`, `npm run lint` and `npm run build` run as a third job in `gates.yml`, on the same Node major the image pins.

## Where the rest lives

- Scope, and whether a thing ships at all: `docs/06-`
- Why LiveKit Cloud, and the adapter boundary: ADR-005 and ADR-002 in `docs/01-`
- The variable list: `agent/.env.example`
- What a call costs to run: `docs/08-`
- Running the demo on the day: `docs/07-demo-runbook.md`
