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

## What is here

| Route | What it is |
|---|---|
| `/` | The call surface: four panels plus the toggle pair |
| `/text` | Text-mode fallback, the venue plan B (docs/01-, docs/07-) |
| `/states` | Every designed escalation and failure state, side by side |
| `/api/text-turn` | Where the text-mode core runs. The browser never calls a provider |

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

One reducer, two sources. Milestone one ships the replay source; milestone two
adds a live one (a LiveKit room for transport signals, an events bridge for
agent events) behind the same `SessionSource` interface, and no panel changes.

Three rules the code holds and the review should check:

1. **Types are mirrored, not invented.** `lib/types.ts` mirrors
   `agent/src/ambassador/schemas.py`; `lib/session/events.ts` copies its field
   names from the `emit(...)` call sites in `agent/src/adapter/events.py`.
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
- It does not draw a stage it did not measure. `Timings.endpoint` and
  `Timings.stt` exist on the Python model but `TurnTracker.finish()` never
  populates them, so they render as "not measured" rather than as zero.
- Number grouping is pinned to one locale. `toLocaleString()` with no locale
  follows the viewer, and under an Indian locale AED 2,000,000 renders as
  20,00,000 - the exact lakh/crore confusion docs/04- treats as a 10x hazard.

## Design

Monochrome base, one metallic accent used sparingly, generous whitespace, no
gradients, no pill buttons, sentence case, no exclamation marks. Reduced motion
replaces the moving waveform with a static level reading rather than a faster
version of the same movement. Keyboard focus is always visible. Verified with no
horizontal overflow at 375px.
