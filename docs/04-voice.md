# 04 - Voice engineering

Everything specific to running the ambassador as speech. The framework owns VAD, endpointing, barge-in, cancellation, transport and reconnection - read this to understand the system and answer questions, not as a list of things to build. `docs/06-` says what to build.

## Latency budget

Planning targets; every figure gets measured against the real stack by the latency meter, never assumed.

| Stage | Budget | Notes |
|---|---|---|
| Endpointing decision | 200-500ms | Semantic endpointing beats fixed silence thresholds |
| STT final after endpoint | 100-300ms **only if streaming** | **The budget line and the chosen model disagree, and the line was written first.** 100-300ms assumes a streaming recogniser: partials arrive during speech, so only the tail after endpoint is charged. Qwen3-ASR is whole-utterance - nothing starts until the buyer stops - so its entire cost is additive. Measured on the hosted path: p50 1081ms, p90 2826ms. A faster host reduces this; only a streaming recogniser removes it from the critical path |
| LLM time to first token | 200-600ms | Qwen 3.7 Flash (ADR-016) with thinking DISABLED - thinking on would add seconds here; prompt caching on the inventory block reduces variance; measure day 1 |
| LLM first complete sentence | +150-400ms | Sentence boundary, not full response |
| Guardrail validation | under 10ms | Regex extraction + set membership |
| Verbalisation | under 5ms | Table lookup |
| TTS time to first audio | 75-300ms | Fish S2.1-Pro claims ~70-90ms (ADR-014); measure, don't trust |
| Network and playback | 100-200ms | WebRTC via LiveKit Cloud |
| **Voice-to-voice first audio** | **target under 1,200ms p50, ceiling 1,500ms** | Measured, displayed |

Two lines to carry into the meeting: the LLM dominates, not the safety layer (guardrails cost ~10ms against a 1,200ms turn - have the meter on screen when you say it); and sentence chunking is the difference between usable and unusable (waiting for the full response adds one to three seconds).

When projected first audio exceeds ~800ms, a short localised acknowledgment may play ("let me look at the collection for you") - sparingly, only on genuinely slow turns, from `data/prerolls.yaml`, native-reviewed. A filler on every turn reads as a tic.

## The dialect problem

The part most likely to embarrass us in the room, and the part a Dubai tech lead is best placed to catch.

- Most Arabic STT is strongest on Modern Standard Arabic. Nobody buys property in MSA - Emirati, Egyptian and Levantine Arabic arrive on the line.
- Arabic-English code-switching is the default Dubai register, not an edge case. One sentence can carry an English project name, an English number and Arabic grammar.
- Hindi buyers code-switch just as heavily and think in lakh and crore.

Mitigations, in order: explicit language selection (ADR-010); per-language STT routing with the Arabic slot decided on real recordings, never datasheets (ADR-015); context biasing loaded with every project and area name (`VERIFY:` exposed through OpenRouter's endpoint - ADR-015); the confirmation policy below; escalation after three failed recognitions. **Test with real speakers before the demo** - an Emirati speaker, a Hindi-English code-switcher, each running the eval script, will find more in an hour than a week of desk testing. If Arabic is marginal, ship it and demonstrate graceful confirmation-and-escalation on a hard case: degrading gracefully on the hardest language reads better in a Dubai boardroom than quietly avoiding Arabic, which will be noticed.

## Buyer-side numbers and currency

The plan is as strict about input numbers as output ones. Two failure modes:

1. **Misrecognition**: "two million" transcribed as "two hundred million".
2. **Unit and currency ambiguity**: "do crore ka budget hai" - two crore of what? INR 2 crore is roughly AED 880k; AED 2 crore is 20 million. Guessing wrong recommends a property off by up to 20x. Same trap with "million" from European and Russian buyers assuming home currency.

**Confirmation policy (ADR-011), deterministic:**

- The first budget mention in a session is always confirmed, and the confirmation names the currency: "Two crore - in rupees or in dirhams?"
- Any currency conversion is deterministic code with a pinned rate marked `VERIFY:`, and the converted figure is spoken back before it drives a recommendation.
- Project names are confirmed when the fuzzy match against inventory is marginal.
- Vendor word-confidence, where available and sane, tightens these triggers; it is never the sole mechanism, because streaming confidence is often absent or uncalibrated.
- Three consecutive failed recognitions escalate warmly. A voice bot that makes the buyer repeat themselves a fourth time earns lasting resentment.

## Verbalisation: a closed set (ADR-009)

Only figures in the allowed set can reach verbalisation, so the reachable figures are enumerable: inventory figures, computed derivations, whitelist. `data/spoken-forms.yaml` gives each language a `forms` table mapping `(kind, value)` to a spoken form, plus a `currency_tokens` list. The spoken form already names the currency, so a written token sitting next to the digits is consumed with them or the buyer hears the currency twice; the token list is per language because the token a native spoken form must swallow is a native word. A native speaker verifies both halves once; anything not in the table falls back to plain digits, which TTS reads acceptably and which by construction should not occur.

| Input | Language | Spoken form |
|---|---|---|
| AED 985,000 | en | nine hundred and eighty-five thousand dirhams |
| AED 2,400,000 | hi | चौबीस लाख दिरहम (24 lakh - and never "2.4 crore", which is 24,000,000; this 10x confusion is exactly why forms are authored once and verified, not generated) |
| AED 985,000 | ar | `VERIFY:` grammatically agreed Arabic form, native-authored |
| 20% | per language | per-locale percentage form |
| Q4 2026 | per language | "the fourth quarter of 2026", never "Q four" |

Ordering restated once: guardrails inspect digits, verbalisation destroys digits, so guardrails run first - enforced by types (`docs/01-`) and asserted by a test.

## Vendor status

- **TTS: decided - Fish Audio S2.1-Pro via `livekit-plugins-fishaudio` (ADR-014).** Open verification items: Hindi voice quality by ear (claimed within 83 languages, not named in Fish's material), Gulf-Arabic voice quality with a native reviewer.
- **LLM: decided - Qwen 3.7 Flash via the OpenAI-compatible endpoint (ADR-016),** one model for conversation and brief extraction, thinking disabled on every voice-path request. Day 1 measures TTFT before building on it; day 3 native review gates Arabic/Hindi generation quality.
- **STT: decided with a gate - Qwen3-ASR-1.7B via OpenRouter's transcription endpoint (ADR-015 amended),** per-utterance transcription on endpoint via a small custom STT node in the adapter layer, routed per language (ADR-010 makes that free). **The Arabic slot is decided by the day 0 head-to-head** - `qwen3-asr-1.7b` vs `qwen3-asr-flash` vs whisper, all on the same OpenRouter key - on real Gulf recordings. Note Flash does not list Hindi, so it can only ever take the Arabic slot. Open items: ar-en/hi-en code-switching tested with real recordings (Qwen3-Omni lineage is strong on zh-en, unproven for our pairs); context biasing with project names `VERIFY:` exposed through OpenRouter; no word confidence (absorbed by the deterministic confirmation policy below). Fish's own speech-to-text is batch-only and is never wired into the live path.

## Pronunciation lexicon

`data/lexicon.yaml` - per language, applied before synthesis. Fish exposes phoneme control for English only among our languages (CMU Arpabet - the `arpabet` field); Arabic and Hindi rely on the `respell` field, alias respelling written into the text ("Bin-GAH-tee").

Minimum set: **Binghatti** (mispronouncing the client's name in their own boardroom is unrecoverable), Bugatti, Jacob&Co, Burj Khalifa, Jumeirah Village Circle, Al Jaddaf, Business Bay, Meydan, Oqood, Trakheesi, Ejari, dirham/AED. Verify each by ear in every shipped voice during rehearsal.

## Barge-in

Framework-provided: caller audio during playback propagates cancellation through generation, synthesis and playback. Budget under 200ms to silence; verify in the day 1 spike. Exception: the opening disclosure ignores barge-in so it always completes (`docs/03-`).

Ours is only the audit consequence: the `TurnRecord` marks the interrupted chunk `completed: false`. The audit claim is chunk-granular - word-level truncation fidelity would require TTS word timestamps and is deliberately not claimed in the POC.

The framework's default false-interruption handling (pause playback, two-second grace, resume if the "interruption" was a cough) is deliberately kept, and the audit adapts to it rather than the reverse: the turn seals when the speech handle resolves, not when the agent state changes - so a resumed false interruption audits `completed: true`, a confirmed interruption audits `completed: false`, and sealing is asynchronous relative to `agent_state_changed`. A session driver that tears down mid-speech must close the session (or call the agent's finalise hook) or the last turn seals with `audit_incomplete: true`.

## Recording, consent, data

- Disclosure + transcription notice at call start, selected language, fixed copy from `data/disclosures.yaml`, native-reviewed, never model-generated. The copy says "transcribed", not "recorded" - the POC stores no raw audio, and the notice must match what is actually retained.
- No raw audio stored. Transcript, guardrail decisions, timings, brief only.
- `VERIFY:` UAE requirements on consent wording and voice-as-biometric under PDPL with a qualified adviser before production.

## Voice and persona

A brand decision - bring options, not a choice: neutral international English, warm and measured; Gulf-accented Arabic rather than a neutral MSA voice; consistent gender and register across languages, or deliberately localised per market. Bring two or three samples to the meeting; it is a decision the client will enjoy making and it converts assumption A8 into engagement. `PHASE-2:` cloning a named ambassador's voice - technically easy, contractually and ethically loaded; raise as roadmap and let them ask.
