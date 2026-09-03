# 06 - Build plan

**Authoritative on scope.** Before building anything, check whether it ships, is faked, or is deferred. Assumption: five working days, largely solo with Claude Code, meeting at the end. If a second engineer appears, spend them on day 3 languages and day 4 UI - the pipeline work does not parallelise.

A head start already exists: the pure core (schemas, inventory loading with computed derivations, both guardrail validators, typed ordering pipeline, closed-set verbaliser, prompts) is written and unit-tested in this repository. Day 2 is wiring and tuning, not writing from scratch.

## Scope

### Ships

| Item | Note |
|---|---|
| Three languages | English, Arabic, Hindi (decision below) |
| Grounded inventory | Ten records, placeholder figures all `VERIFY:`, incl. 3 branded-enquiry projects with no price |
| Numeric-claims validator | In the pipeline hook, before synthesis. The centrepiece. Already written; wire and tune |
| Prohibited-language validator | English patterns only, disclosed (docs/03-) |
| `GUARDRAIL_MODE` + `PROMPT_MODE` toggles | The defence-in-depth demo depends on the pair. Not optional |
| Escalation | All eight triggers: unknown project, branded pricing, unit availability, negotiation, contractual/legal, explicit request, complaint, 3x recognition failure |
| Lead brief | Async small-model extraction, validated, on the ambassador view |
| Payment answers | From computed derived figures in inventory |
| Confirmation policy | Deterministic: first budget mention + currency; marginal name matches |
| Latency meter | Per-component, on screen |
| Barge-in | Framework-provided; we add the chunk-level audit record |
| Verbalisation + lexicon | Three languages, closed-set tables, native-verified |
| Text-mode fallback | Same core as chat; the venue plan B |
| Hosting | We host, on Railway. Media stays on LiveKit Cloud (ADR-005): Railway has no UDP ingress, so it cannot serve a media server. Topology in `docs/09-deploy.md` |
| `agent-worker` service | The LiveKit Agents worker (`adapter/agent.py`). Outbound only: it joins rooms and exposes no port, so it takes no public domain |
| `web` service | The Next.js surface, with the viewer token minted in its own server route (`api/session/room`, `livekit-server-sdk`). Public Railway domain. No separate token service |
| Deployment secrets | Railway service variables only; keys never enter the repo. The env contract is `agent/.env.example`, referenced rather than copied |
| Hosted client demo | The Railway URL is shared with the client, who tries the POC at their end with nobody from us in the room. So the hosted stack has to be a complete experience on its own, not a viewer onto a call happening somewhere else (`task-hosted-talk-page`) |
| Browser talk path | The client enters an access code, picks a language, and talks. A server route creates a fresh room and mints a publish-capable token; the browser publishes its microphone with `livekit-client` and plays the agent back. The worker is dispatched automatically, so nothing has to tell it which room to join (`docs/09-deploy.md`) |
| Named ambassadors | Each language's ambassador has a given name, in `data/ambassadors.yaml`. English is Jane, chosen by the client. The name is product identity rather than language copy, so it is not the build team authoring in a language it does not speak - but HOW it is written and said in Arabic and Hindi is a native-reviewer question, and until those are answered those entries are empty and the agent is unnamed there, exactly as today |
| Per-call language | The room carries its language in room metadata and the entrypoint reads it, falling back to `LANGUAGE`. Three languages from one worker, instead of one worker per language (`task-hosted-language-from-metadata`) |
| Hosted transcript source | The framework's own `lk.transcription` text streams, which carry the buyer-visible words and nothing else. No event bridge on the hosted service (`task-hosted-language-from-metadata` verifies it) |
| Hosted access controls | A public URL in front of paid providers, so: an access code checked server-side, a cap on concurrent demo rooms, a short token TTL, short room timeouts, and a per-call duration cap. Named in `docs/09-deploy.md` |
| Graceful call ending | The agent detects that the buyer is closing the conversation, speaks an authored farewell in the call's language, and ends the job so the room closes and the browser sees the disconnect. Asked for by the client after their first hosted call: today nothing ends a call but the buyer closing the tab, so a client who says goodbye hears silence (`task-graceful-goodbye`) |
| Voice-agent talk surface | Once a call starts, `/talk` is an orb and its subtitles: a dark disc with a coloured corona that breathes when listening, blooms with the ambassador's voice, and shows distinct states for the visitor speaking and for thinking. The transcript reads as subtitles under it - the current utterance large and centred, the previous lines fading above - built on the segment-keyed rail. The ambassador has a name from `data/ambassadors.yaml`, English **Jane**, and it labels the orb and her lines. `prefers-reduced-motion` gets a static glow whose state is carried by colour and label instead of movement (`task-talk-orb-experience`) |

### Faked (`STUB:`)

Booking = spoken read-back, no calendar. CRM write = console log behind an interface. Inventory = hand-authored file, not a feed.

### Deferred (do not build; present as roadmap)

Remaining three languages - SIP/80015 - WhatsApp follow-up - durable event store + PII hashing - per-referenced-project allowed-set scoping - `compute_payment` tool - POC 2 - everything `PHASE-2:`.

On the hosted client demo specifically, three things are deliberately absent rather than unfinished. **The latency meter, the guardrail and violation panels, and the ambassador brief stay laptop-only**: they carry the unredacted records that issue #30 keeps loopback-bound, and they are the tech lead's screen in the meeting rather than the client's. **Hosted text mode** stays laptop-only for the same reason it exists, being a fallback for a room with bad audio; on the hosted service it refuses with a reason instead of serving a script. **A transport for the event bridge between services** is not built, because the bridge's loopback restriction is a security property and replacing it is design work this POC does not need once the transcript comes from the framework. The hosted page says which panels it is not showing, in one sentence.

On hosting specifically: one Railway project with one environment, so no staging tier. No custom domain (the generated Railway domain is the demo URL), no autoscaling, and no replica count above one. The web gates are no longer on that list: `npm test`, `npm run typecheck`, `npm run lint` and `npm run build` run as a third job in `gates.yml`. All of these are consequences of the two-service topology rather than separate choices; `docs/09-deploy.md` is where they are argued.

## Third language: Hindi

1. A Hindi-English code-switcher can be put in a room this week; that is not certain for Russian or Mandarin, and an untested language is a liability on stage.
2. Indian buyers are consistently the largest nationality group in Dubai property.
3. The lakh/crore verbalisation and the currency-confirmation moment are memorable, and the team can verify them internally.

Swap to Russian only if Binghatti signals that channel matters more AND a native speaker is secured for day 3. Decide before day 1 - it sets TTS voice procurement.

## The days

### Day 0 (two hours, before the week starts)

1. ~~Run ten real Gulf-Arabic recordings through `qwen3-asr-1.7b`, `qwen3-asr-flash` and whisper - all on the one OpenRouter key; the winner takes `STT_MODEL_AR` (ADR-015).~~ **Cancelled 2026-08-29 by ADR-017.** The bake-off was between three models on a path that measured 6-25x over the post-endpoint budget whichever model ran, so no winner was shippable; STT is Deepgram `nova-3` streaming and `STT_MODEL_AR` stays empty. The risk it was meant to retire (R1) is NOT retired and moves with the vendor: Arabic dialect quality and ar-en/hi-en code-switching are still unproven, now on Deepgram, and still want ten real Gulf-Arabic recordings and a code-switching speaker before the demo. Keyterm boosting covers the brand name that the old path could not hear.
2. Listen to Fish S2.1-Pro Gulf-Arabic and Hindi voices on real sentences from the spoken-forms tables. Hindi is claimed but not named in Fish's material; if it disappoints, the swap decision happens now, not on day 3.
3. Request the price sheet from Binghatti's contact (A3).

### Day 1 - framework spike. Gate: all three hooks, or switch

Audio round-trip on LiveKit Agents: microphone in, English speech out. Then prove, in the real framework, the three integration points the architecture depends on:

1. Text interception between LLM and TTS (wrap `process_sentence` in, even with a trivial validator).
2. A function tool firing mid-turn while speech streams.
3. A post-turn async task running (stub brief extraction).

**If any of the three fails by end of day, switch to Pipecat immediately.** Fighting a framework on day 2 is how the week fails. (Fish Audio survives that switch: Pipecat has `FishAudioTTSService`.) Also today: Fish account + `FISH_API_KEY` provisioned and `livekit-plugins-fishaudio` streaming and cancelling verified as part of the hook gate; the custom STT node against OpenRouter's transcription endpoint written and verified inside the hook gate (per-utterance call, endpoint shape is base64 JSON, not OpenAI multipart - ADR-015), per-utterance STT latency measured against the 100-300ms line, and context biasing with project names confirmed exposed or written off (ADR-015); OpenRouter account + `OPENROUTER_API_KEY` provisioned, **thinking confirmed OFF through the proxy (no reasoning tokens in the response usage) and TTFT measured with the real serialised inventory prompt, cache warm** - the public P50 for this slug is 0.67s, likely inflated by thinking-on traffic, so this measurement is a genuine go/no-go: if it misses the 200-600ms line, try direct DashScope, then another model slug on the same key, today, not on day 4 (ADR-016); voices shortlisted (DONE - `docs/voice-shortlist.md`, three candidates per language with free previews; the top register match in each is wired as a PROVISIONAL default so no language falls through to Fish's own voice, and the client still chooses at the meeting); Railway hosting path confirmed; third language locked; native-speaker session for day 3 booked.

### Day 2 - grounding and guardrails live. Gate: the trap works

- `data/inventory.json` to ten records (3 branded, no price in the object graph)
- Serialised prompt block + ambassador and naive prompts wired
- Core pipeline in the interception hook, per sentence
- Both toggles wired end to end
- Violation logging visible (false-positive tuning starts today)

**Gate: the hallucination trap works aloud today, including the leading-question variant, 5 runs out of 5 in each mode.** If that holds on day 2, the meeting has a spine no matter what else slips. Ordering discipline for the week: if a day slips, cut languages, never guardrails.

### Day 3 - languages. Gate: ship-or-drop Arabic tonight

- Arabic and Hindi: STT hints, TTS voices, per-language prompt handling
- Spoken-forms tables completed for the full 10-record figure set (closed set - this is data authoring plus native review, not engine work)
- Lexicon: "Binghatti" first, then project and area names
- Native-speaker session runs today (booked day 1)

**Gate: decide Arabic tonight - ship it or drop to two languages.** If dialect accuracy is marginal, ship it and rehearse the confirmation-and-escalation path on a hard case; graceful degradation on the hardest language beats quiet avoidance in a Dubai boardroom.

### Day 4 - brief, UI, fallbacks. Gate: feature freeze

- Async brief extraction wired and validated; shortlist ids checked against inventory
- Booking read-back tool
- UI: call panel, transcript rail, ambassador view, latency meter, mode toggles
- Text-mode fallback page
- Escalation and failure states designed, not default

**Gate: feature freeze at end of day.** Anything unfinished becomes a roadmap line. The day 5 feature is how demos break.

### Day 5 - rehearsal

- Run the gate eval categories (docs/05-); fix only what breaks the demo path
- Screen recording with audio saved locally
- Runbook (docs/07-) three consecutive clean runs
- Fill the cost model (docs/08-) with measured per-call numbers from rehearsal sessions
- Verify every lexicon entry by ear in all three voices

## Risk register

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | Arabic dialect STT unusable on real speakers | High | Day 0 hour; day 3 gate; confirmation/escalation path as the on-stage story |
| R2 | A framework hook does not behave as documented | Medium | Day 1 gate covers all three hooks; switch, never adapt |
| R3 | Trap demo fizzles (naive mode still refuses) | Medium | Leading-question variant; rehearse 5/5; recorded run as backup |
| R4 | Venue audio/network | Medium | External mic tested in a room; phone hotspot primary; text-mode fallback; recording |
| R5 | Scope creep on days 4-5 | High | The freeze is a gate, not a suggestion |
| R6 | Placeholder figures challenged by someone who knows real prices | Medium | Chase the price sheet (day 0); otherwise label figures illustrative on screen |
| R7 | "Binghatti" mispronounced in one voice | Medium | Lexicon day 3 (Arpabet for en, respelling for ar/hi - Fish has no Arabic/Hindi phoneme control); verified by ear in all voices day 5 |
| R8 | Fish Hindi or Gulf-Arabic voice quality disappoints | Medium | Day 0 listen; swap is config + voice ids (ADR-006/014), decided before day 3, never during it |

## Definition of done

- [ ] Demo path runs clean three consecutive times on the demo machine
- [ ] Trap works in all shipped languages, both mode pairs
- [ ] Branded pricing yields no figure, range, or comparison
- [ ] Payment question answered from derived figures, live
- [ ] Currency confirmation fires on a crore/lakh budget statement
- [ ] "Binghatti" correct in every shipped voice
- [ ] p50 voice-to-voice first audio measured and displayed
- [ ] Gate eval categories at 100%, report exported for the meeting
- [ ] Screen recording with audio saved locally
- [ ] Every `VERIFY:` figure visibly marked in the build, not presented as fact
- [ ] Cut list written and ready to present

## Presenting the cut

Bring the cut list as a slide. Two framings worth rehearsing:

> "Three languages ship today. The other three are configuration plus a native reviewer - a day each, not a sprint. We stopped at three because we could test three properly."

> "The prohibited-language patterns are English-only so far. The validator that stops a fabricated price is language-agnostic - it works on digits - so that guarantee holds in all three languages. We did not want to ship an Arabic pattern file nobody on the team has read."

The second sentence pair is the more valuable one: it shows you distinguish the guardrail that carries the guarantee from the one that carries the tone.
