# 04 - Voice engineering

Everything specific to running the ambassador as speech. The framework owns VAD, endpointing, barge-in, cancellation, transport and reconnection - read this to understand the system and answer questions, not as a list of things to build. `docs/06-` says what to build.

## Latency budget

Planning targets; every figure gets measured against the real stack by the latency meter, never assumed.

| Stage | Budget | Notes |
|---|---|---|
| Endpointing decision | 200-500ms | Semantic endpointing beats fixed silence thresholds |
| STT final after endpoint | 100-300ms **only if streaming** - MET: Deepgram measured 258-327ms (ADR-017) | **The budget line and the chosen model disagree, and the line was written first.** 100-300ms assumes a streaming recogniser: partials arrive during speech, so only the tail after endpoint is charged. Qwen3-ASR is whole-utterance - nothing starts until the buyer stops - so its entire cost is additive. Measured on the hosted path: p50 1081ms, p90 2826ms. A faster host reduces this; only a streaming recogniser removes it from the critical path |
| LLM time to first token | 200-600ms | measured 685ms with caching live (ADR-017). Qwen 3.7 Flash (ADR-016) with thinking DISABLED - thinking on would add seconds here; prompt caching on the inventory block reduces variance; measure day 1 |
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

Mitigations, in order: explicit language selection (ADR-010); per-language STT routing with the Arabic slot decided on real recordings, never datasheets (ADR-015); keyterm boosting loaded with every project and area name (working on Deepgram, ADR-017; the previous path had no such mechanism); the confirmation policy below; escalation after three failed recognitions. **Test with real speakers before the demo** - an Emirati speaker, a Hindi-English code-switcher, each running the eval script, will find more in an hour than a week of desk testing. If Arabic is marginal, ship it and demonstrate graceful confirmation-and-escalation on a hard case: degrading gracefully on the hardest language reads better in a Dubai boardroom than quietly avoiding Arabic, which will be noticed.

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
- **STT: decided - Deepgram `nova-3` streaming via the first-party LiveKit plugin (ADR-017),** selected by `STT_PROVIDER=deepgram`. Measured 258-327ms after audio ends, inside the budget line, because streaming charges only the tail. Keyterm boosting recognises "Binghatti" (the whole-utterance path returned "Bint Jbeil" and OpenRouter ignored its biasing parameter), and numerals return figures as digits rather than words. The Qwen3-ASR whole-utterance path (ADR-015) stays selectable and tested but is not the default. **Still open: Arabic dialect quality on real Gulf recordings, and ar-en/hi-en code-switching - neither is settled by these English measurements.** Fish's own speech-to-text is batch-only and is never wired into the live path.

## Pronunciation lexicon

`data/lexicon.yaml` - per language, applied before synthesis by `adapter/lexicon.py` in the `tts_node`, after guardrails and verbalisation. Respelling destroys the word the way verbalisation destroys the digits, so it comes last: the transcript, the audit and the ambassador view keep the real words, and only the synthesiser sees "bin-GAH-tee". Fish exposes phoneme control for English only among our languages (CMU Arpabet - the `arpabet` field); Arabic and Hindi rely on the `respell` field.

**This file also had no loader, so none of it was reaching Fish in any language.** "Binghatti" was synthesised from its literal spelling, and the by-ear verification this section asks for could not be carried out, because the thing under test was not in the path.

**Respellings are per language, and that is not a formality.** A respelling is instructions to a voice in that voice's own orthography. Handing "bin-GAH-tee" to an Arabic voice does not fix a mispronunciation, it selects a different one, quite possibly a spelled-out one. So a term is respelled only in a language whose entry someone competent in that language wrote, and passed through untouched otherwise - which is exactly the old behaviour, so an unauthored language is no worse off. The `en` entries are the build team's own; ar and hi are native-authored deliverables like the rest of `data/`.

Two properties are held by tests rather than by care: a term is still respelled when the text stream happens to split across it (today's chunks are whole sentences so it cannot happen, which is why nothing else would notice if that changed), and the first sentence is never held back, because TTS first audio is one of the two largest remaining components of the budget.

Minimum set: **Binghatti** (mispronouncing the client's name in their own boardroom is unrecoverable), Bugatti, Jacob&Co, Burj Khalifa, Jumeirah Village Circle, Al Jaddaf, Business Bay, Meydan, Oqood, Trakheesi, Ejari, dirham/AED. Verify each by ear in every shipped voice during rehearsal.

## Barge-in

Framework-provided: caller audio during playback propagates cancellation through generation, synthesis and playback. Budget under 200ms to silence; verify in the day 1 spike. Exception: the opening disclosure ignores barge-in so it always completes (`docs/03-`).

Ours is only the audit consequence: the `TurnRecord` marks the interrupted chunk `completed: false`. The audit claim is chunk-granular - word-level truncation fidelity would require TTS word timestamps and is deliberately not claimed in the POC.

The framework's default false-interruption handling (pause playback, two-second grace, resume if the "interruption" was a cough) is deliberately kept, and the audit adapts to it rather than the reverse: the turn seals when the speech handle resolves, not when the agent state changes - so a resumed false interruption audits `completed: true`, a confirmed interruption audits `completed: false`, and sealing is asynchronous relative to `agent_state_changed`. A session driver that tears down mid-speech must close the session (or call the agent's finalise hook) or the last turn seals with `audit_incomplete: true`.

## Recording, consent, data

- Disclosure + transcription notice at call start, selected language, fixed copy from `data/disclosures.yaml`, native-reviewed, never model-generated. The copy says "transcribed", not "recorded" - the POC stores no raw audio, and the notice must match what is actually retained. Spoken from the agent's `on_enter` hook with `allow_interruptions=False`, so it completes even under barge-in.

  **This paragraph described something that was not happening.** `data/disclosures.yaml` had no loader anywhere in the codebase, no call site spoke it, and the system prompt told the model the opening was handled for it - so the model was instructed not to disclose and nothing else did. The result was a voice agent that never disclosed it was one, in any language, English included. Wired in `adapter/disclosure.py`; every branch of the original omission was individually reasonable, which is why nothing caught it.

- **An empty disclosure blocks its language.** A language with no native-authored copy cannot open a call, because the disclosure *is* the thing it would be opening without, so `LANGUAGE=ar` now refuses to start rather than degrading quietly. `ALLOW_UNCERTIFIED_LANGUAGE=true` opens in English instead and marks the event stream `uncertified_fallback: true` - that is the graceful-degradation demo this document argues for, not a way to ship an unreviewed language. Presence of copy in that file is consequently the readiness signal for a language, which makes the ship-Arabic-or-drop-it decision a state of the repository rather than a note in a meeting.
- No raw audio stored. Transcript, guardrail decisions, timings, brief only.
- `VERIFY:` UAE requirements on consent wording and voice-as-biometric under PDPL with a qualified adviser before production.

## Voice and persona

A brand decision - bring options, not a choice: neutral international English, warm and measured; Gulf-accented Arabic rather than a neutral MSA voice; consistent gender and register across languages, or deliberately localised per market. Bring two or three samples to the meeting; it is a decision the client will enjoy making and it converts assumption A8 into engagement. `PHASE-2:` cloning a named ambassador's voice - technically easy, contractually and ethically loaded; raise as roadmap and let them ask.
