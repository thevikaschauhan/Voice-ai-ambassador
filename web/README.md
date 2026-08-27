# Demo surface (built day 4)

Next.js 15 + React 19 + Tailwind, UI only - no provider calls (AGENTS.md). Scaffold with `create-next-app` on day 4; the LiveKit React SDK handles the client side of the audio session.

Four panels, each earning its place with a different person in the room (docs/07-):

| Panel | Audience | Contents |
|---|---|---|
| Call | Everyone | Talk button, waveform, barge-in indicator, language selector, end call |
| Transcript rail | The room | Buyer and agent turns as text |
| Ambassador view | The commercial stakeholder | Lead brief, shortlist with figures, stage bar, escalation state |
| Latency meter | The tech lead | Per-component: endpoint, STT, LLM first sentence, guardrail, TTS, total |

Plus the mode toggles (`GUARDRAIL_MODE`, `PROMPT_MODE`) labelled honestly ("typical chatbot configuration"), and a text-mode fallback page that talks to the same core (docs/06-).

Design: monochrome base, one metallic accent, generous whitespace, no gradients, no pill buttons, sentence case, no exclamation marks. Binghatti sells restraint.
