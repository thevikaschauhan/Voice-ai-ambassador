# Demo surface

Next.js 15 + React 19 + Tailwind 4, UI only. The page makes no provider calls and
opens no microphone (AGENTS.md hard rule): anything that needs a key runs in a
route handler on the server.

## Run

```sh
npm install
npm run dev          # http://localhost:3000
npm test             # vitest, rendered-output assertions
npm run typecheck
npm run lint
npm run build
```

`data/inventory.json` and `data/disclosures.yaml` are read from the repo root at
request time, so the dev server must be started from inside `web/`.

**The `next` scripts pin `NODE_ENV` and that is load-bearing.** `next build` is
a production build by definition, and inheriting a non-production `NODE_ENV`
(some agent shells export `NODE_ENV=development`) makes it prerender `/404` from
the dev pages-router error component and fail with `<Html> should not be
imported outside of pages/_document`. The error names a pages-router problem
this app does not have, which is what makes it expensive to diagnose. Do not
remove the prefixes.

## What is here

| Route | What it is |
|---|---|
| `/` | The call surface: four panels plus the toggle pair |
| `/text` | Text-mode fallback, the venue plan B (docs/01-, docs/07-) |
| `/states` | Every designed escalation and failure state, side by side |
| `/api/text-turn` | Where the text-mode core runs. The browser never calls a provider |
| `/api/session/stream` | Re-serves the agent's live event stream, same-origin. Holds the token |
| `/api/session/room` | Mints a listen-only LiveKit ticket. Holds the API secret |

Four panels, each earning its place with a different person in the room:

| Panel | Audience | Contents |
|---|---|---|
| Call | Everyone | Talk button, waveform, barge-in indicator, language, end call |
| Transcript | The room | Buyer and agent turns, and every guardrail decision that changed one |
| Ambassador view | The commercial stakeholder | Lead brief, shortlist with real figures, stage bar, escalation state |
| Latency | The technical lead | Per-component timings, and what was not measured |

## How data reaches the panels

```
replay script  ─┐
                ├─►  reduce()  ─►  SessionState  ─►  panels
live source    ─┘   (lib/session/state.ts)
```

One reducer, two sources, and no panel knows which is running. The page decides
on the server: if the agent left a handshake, the surface attaches to it;
otherwise it plays a fixture and says so.

```
agent (127.0.0.1, token required)  ──►  this server (holds the token)
                                   ──►  browser (same-origin, no token)
```

**The token stops at the server.** A token in a browser is a token in every page
that shares it and in the devtools of anyone standing behind the laptop, so the
credential is read from the agent's `0600` handshake file by `lib/bridge/`,
which is `server-only`, and what crosses to the client is events. A test asserts
the token appears in no response body; so does the browser check in the PR.

**Provenance is on screen at all times.** `Live agent` or `Replay`, in the
header, plus a sentence saying which and why. The one unrecoverable mistake this
surface could make is letting somebody believe a fixture was a call.

Three rules the code holds and the review should check:

1. **Types are mirrored, not invented.** `lib/types.ts` mirrors
   `agent/src/ambassador/schemas.py`; `lib/session/events.ts` copies its field
   names from the `emit(...)` call sites in `agent/src/adapter/events.py`.
   Two unions, deliberately: `SessionInput` carries the unknown arm because a
   bridge may hand us an event type we have never seen, and `AuthoredInput` is
   closed because fixtures and the text-mode core are written here - typing
   those as `SessionInput` lets a missing field match the unknown arm, compile
   clean, and fold as a no-op at runtime.
2. **The surface reads in-process records, not the emitted stream.** That stream
   is redacted by design (validator 4) and carries no free text. Where a field
   is redacted on the way to stdout, this surface keeps it, and says so.
3. **No figure lives here.** The agent's shortlist is a list of ids;
   `lib/inventory.ts` turns those ids into figures by reading
   `data/inventory.json` on the server. Derived amounts apply the plan's own
   percentages rather than carrying a table (invariant 2). An id that does not
   resolve is shown, not dropped.

## What the panels refuse to do

- The latency meter does not stack the stages. `llm_first_sentence` and
  `tts_first_audio` are cumulative marks from turn start, not durations, so
  adding them would double-count. They are drawn on a timeline at their real
  offsets.
- It does not add `stt` to `endpoint`. The framework takes both from the same
  anchor, so transcription is a **component** of the endpointing figure (#21).
  They are drawn nested; summing them would invent a stage that does not exist.
- It does not draw a stage it did not measure. A typed turn has no
  end-of-utterance, and the framework reports nothing when its VAD anchors are
  missing, so those turns say "not measured" rather than showing a zero. Text
  mode is where that path is exercised.
- Voice-to-voice first audio is `endpoint + tts_first_audio`, not
  `tts_first_audio`. Endpointing happens before the turn tracker's clock
  starts, so leaving it out understates the buyer's wait by up to half a
  second.
- Number grouping is pinned to one locale. `toLocaleString()` with no locale
  follows the viewer, and under an Indian locale AED 2,000,000 renders as
  20,00,000 - the exact lakh/crore confusion docs/04- treats as a 10x hazard.

## Two feeds, and what each one is for

The live surface reads two things, because they are separately available:

| Feed | Carries | Absent when |
|---|---|---|
| the events bridge | turns, guardrail decisions, brief, timings | no agent is running |
| the LiveKit room | amplitude, who is talking, room connection | no room is open, or LiveKit is unconfigured |

A machine with no room still gets a full transcript. `lib/session/source.ts`
`combine()` folds both into one reducer, and the bridge stops *inferring* who is
speaking when the room is there to *measure* it - two sources writing one
indicator means `turn_complete` can silence a speaker who is still talking.

**The viewer token is minted server-side with the narrowest grant that works:**
one named room, `canPublish: false`, `canPublishData: false`, `hidden: true`,
ten-minute TTL. The surface watches a call; it has no microphone and must not be
able to put audio in front of a buyer. The API secret that signs it never leaves
the server, and `tests/room-grant.test.ts` decodes a real signed token to check
each of those claims rather than trusting the call site.

Nothing is played out of the laptop's speakers: levels come from an
`AnalyserNode` that is deliberately not connected to `destination`. The audio is
already in the room, and a demo machine echoing the agent back on stage is a
failure mode nobody needs.

## What the live path does not claim

- **No audio track, when there is none.** With no room joined the waveform says
  "no audio track attached" rather than drawing a flat trace that would read as
  a silent microphone. That is a real condition on the reducer
  (`audioSource: 'none'`), not a mode flag, so it is also what you get when
  LiveKit is configured but the room cannot be joined.
- **Speaking indicators are turn-level until the room is attached.** A turn is
  already transcribed when it is emitted, so "buyer speaking" is known once they
  have stopped. Barge-in is exact, because the agent audits the chunk it cut.
  The panel says which of the two it is showing.
- **The mode toggles are read-only against a running agent.** Both modes are
  read once at session start by the agent process, so nothing here can change a
  call already in flight. They report what the agent said it is running. A
  control that looks live and does nothing is worse than one that says it
  cannot.

## Design

Monochrome base, one metallic accent used sparingly, generous whitespace, no
gradients, no pill buttons, sentence case, no exclamation marks. Reduced motion
replaces the moving waveform with a static level reading rather than a faster
version of the same movement. Keyboard focus is always visible. Verified with no
horizontal overflow at 375px.
