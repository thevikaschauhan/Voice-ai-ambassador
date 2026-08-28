# 01 - Technical design

## The system in one paragraph

A cascaded voice pipeline - streaming STT, LLM, guardrails, verbalisation, streaming TTS - built on LiveKit Agents, with all differentiating logic in a pure-Python core that has no framework imports. The spoken reply is a plain text stream; discrete actions (escalate, offer booking) are framework function tools; the lead brief is extracted by a cheap model call off the latency path. Every sentence passes deterministic validation before synthesis, and the types make it impossible to synthesise anything that has not.

## Design principle 1: the headless core

`agent/src/ambassador/` contains everything we would defend in front of a technical evaluator - grounding, guardrails, verbalisation, prompts, schemas - and imports nothing from LiveKit. A thin adapter (`agent/src/adapter/`, written day 1) wraps the core into the framework's hooks.

What this buys, in order of importance:

1. **Evals run headless.** The eval harness (`docs/05-`) exercises text-in, validated-text-out with no audio stack, so it runs in CI and on every prompt change.
2. **A live text-mode fallback.** If venue audio dies, the same core demos as text chat. A far stronger plan B than a screen recording alone (the recording still exists).
3. **Framework portability.** If LiveKit fails the day 1 gate, the Pipecat adapter is a rewrite of the thin layer only.

## Design principle 2: the two-channel turn

The model's output is split by kind, because each kind has a different natural transport:

| Output | Channel | Why |
|---|---|---|
| Spoken reply | Plain streamed text through the framework's LLM-to-TTS path, guardrail processor in between | The well-trodden framework path; sentence chunking and cancellation come free |
| Actions: `escalate_to_human`, `offer_booking`, `confirm_booking` | Framework function tools | Both frameworks treat "speak and call tools in one turn" as first-class; the action signal arrives mid-turn, not after stream end |
| Lead brief | Separate small-model extraction call, async after each turn, Pydantic-validated | Off the latency path entirely; the ambassador screen tolerates a sub-second lag; negligible cost |

**Rejected alternative:** one forced tool call per turn with the reply as the first schema property, incrementally parsed from streaming JSON. Rejected because it requires custom per-provider delta parsing and JSON unescaping inside the framework's LLM node, relies on non-contractual property ordering, and puts the most fragile machinery on the critical path. The two-channel design keeps every custom component off the framework's hot path.

## The turn flow

```
 1  Framework: VAD + endpointing detect end of buyer speech
 2  Framework: STT transcribes the finalised utterance (Qwen3-ASR on endpoint, ADR-015)
 3  Core: confirmation policy (docs/04-) - deterministic; may emit a confirmation turn and stop here
 4  Core: compose prompt (serialised inventory incl. computed derived figures + conversation)
 5  Framework: LLM streams plain text; function tools may fire (escalate, booking)
 6  Adapter, per completed sentence:
      core.process_sentence(raw, ctx)
        a  numeric-claims + prohibited-language guardrails    ~10ms
        b  pass -> verbalise (closed-set lookup)              ~1ms
        c  returns SpeakableText -> framework TTS -> audio out
      violation -> see regeneration policy below
 7  Adapter, after the turn's speech handle resolves: async brief extraction (small model),
    Pydantic-validated, pushed to the ambassador view. The extraction input is the context
    the turn was parked with - a turn force-sealed by a newer one keeps its own transcript
 8  Events emitted with per-component timings; audit record of what was actually
    spoken (chunk granularity - see docs/04- barge-in)
```

Step 6 is the architecture; everything else is transport. When the tech lead asks "how do you stop it speaking a price it made up", the answer is `process_sentence()`, and you should be able to open the file.

**Regeneration policy on guardrail violation.** If nothing has been synthesised yet this turn: cancel, regenerate once with the violation named in the retry prompt, then composed fallback. If audio has already played: skip regeneration entirely and speak a composed bridge ("Let me be precise about that figure") plus the correct escalation. A blind mid-turn regeneration repeats or contradicts what the buyer already heard; the bridge cannot.

**Terminal LLM failure** (retry budget exhausted) speaks the composed fallback and escalates regardless of whether audio has already played - unlike a guardrail block, there is nothing left to bridge to when the model is gone. The `llm_failure` event carries `spoken_before` and the `fallback` event carries its reason, so the audit distinguishes the two situations. The retry budget itself is deliberately bounded: two framework passes of at most two SDK attempts each, with Retry-After clamped, roughly two seconds of backoff worst case before round-trips - a frozen turn is a worse outcome than an honest fallback.

**Arithmetic questions.** The buyer's most predictable follow-up ("so what do I pay upfront?") is answered from derived figures computed at inventory load time (`inventory.py`), present in both the prompt and the allowed set. `PHASE-2:` a `compute_payment` function tool backed by deterministic code whose results extend the turn's allowed set dynamically.

## Ordering enforced by types, not discipline

Guardrails inspect digits. Verbalisation destroys digits. Audio cannot be retracted. The core makes the wrong order unrepresentable rather than merely tested:

```python
run_guardrails(raw: str, ctx)      -> ValidatedSentence | GuardrailViolation
verbalise(v: ValidatedSentence)    -> SpeakableText          # rejects raw str at runtime
process_sentence(raw, ctx)         -> SpeakableText | GuardrailViolation   # the ONLY public path
```

The TTS adapter accepts `SpeakableText` only. An engineer who reorders the stages gets a type error, not a silently disabled guardrail. A test asserts the ordering anyway (`test_pipeline_ordering.py`); do not delete it.

## Module layout

```
agent/
  src/ambassador/            # pure core - ZERO framework imports
    schemas.py               # Pydantic models (docs/02- is the human-readable contract)
    inventory.py             # load data/inventory.json, validate, COMPUTE derived figures,
                             # serialise prompt block, build allowed-figure sets
    verbalise.py             # closed-set spoken-form lookup, digit fallback
    prompts.py               # ambassador + naive system prompts
    guardrails/
      numeric_claims.py      # extraction (western/Arabic-Indic/Devanagari digits), normalisation,
                             # allowed-set check
      prohibited.py          # pattern loader (data/prohibited-patterns.yaml) + checker
      pipeline.py            # ValidatedSentence / SpeakableText types, process_sentence()
  src/adapter/               # day 1+: LiveKit session, tool definitions, brief extraction task
  tests/
data/
  inventory.json             # system of record for the POC; placeholder figures, all VERIFY:
  prohibited-patterns.yaml   # English patterns, reviewable by a non-engineer
  spoken-forms.yaml          # (language, kind, value) -> spoken form; ar/hi entries VERIFY:
  whitelist.yaml             # allowed figures beyond inventory, each with a justification
web/                         # Next.js demo surface, day 4
```

Data files are language-neutral (JSON/YAML) so the same patterns and forms serve any future TypeScript consumer without duplication.

## Architecture decision records

### ADR-001 - Cascaded pipeline, not speech-to-speech
A speech-to-speech model emits audio as it generates: a fabricated price reaches the buyer at the moment it is produced, with no inspection point and nothing to retract. Cascaded preserves a text checkpoint between generation and synthesis, which is where the guardrails live. This is forced by the central claim, not preferred. Sentence-level chunking recovers most of the latency; the paralinguistic nuance lost through the text bottleneck is accepted. **Revisit when** a speech-to-speech model exposes a pre-emission text channel with a cancellation window - that capability, not general quality.

### ADR-002 - Headless core with a thin framework adapter
See design principle 1. The consequence that matters: no LiveKit import may appear under `src/ambassador/`, enforced by a test.

### ADR-003 - Two-channel turn design
See design principle 2. Supersedes any single-forced-tool-call design.

### ADR-004 - No vector database
The full inventory is serialised into the system prompt. At ~10-40 projects of ~15 short fields, that is 6-12k tokens; retrieval adds infrastructure, latency, and a new failure class (the right project not retrieved) for nothing at this scale. Prompt caching makes the cost negligible. **Thresholds for revisiting:** more than ~60 projects; per-unit inventory; brochures/floor plans/FAQ documents entering the corpus; serialised catalogue beyond ~25k tokens. Say the threshold out loud in the meeting - a tech lead who has been pitched RAG by four vendors will respect a vendor who explains why they did not use it.

### ADR-005 - LiveKit Agents, Pipecat as fallback
Chosen for documentation density (the binding constraint on agent-assisted build speed), managed transport via LiveKit Cloud (WebRTC - much better on hostile venue networks than a raw WebSocket), a React SDK that directly serves the demo UI, built-in per-stage metrics that feed the latency meter, and native SIP support that makes "the 80015 line is an integration, not a rebuild" literally true. Both frameworks expose the required hooks; the day 1 gate verifies all three (text interception, function tools, post-turn task) on the real framework, and failure means switching immediately, not adapting.

### ADR-006 - Provider swappability is framework plugin configuration
LiveKit's plugin system already isolates STT/LLM/TTS providers behind uniform interfaces. Hand-rolling another abstraction inside the agent would duplicate the framework and violate "do not build what the framework provides". Provider choice lives in config/env. (A hand-rolled `complete()` adapter remains the right call for the future POC 2 TypeScript service, where no framework provides one.)

### ADR-007 - Guardrails as post-generation validators
Prompt instructions reduce violation rates; they do not eliminate them, they degrade under adversarial input, and they cannot be audited. The claim "this cannot speak a price that is not in your system" is only supportable by code. Two validators are non-negotiable: numeric claims and prohibited language. Full detail in `docs/03-`.

### ADR-008 - Global allowed set for numeric claims in the POC
The allowed set is the union of every figure across the whole inventory (source and computed), plus a short justified whitelist. Per-referenced-project scoping would additionally catch cross-project figure confusion but depends on reliable reference resolution - a fuzzy component at the most load-bearing point, and the source of the false positives that get validators disabled. Global scoping cannot false-positive on a real figure and still catches every invented one. `PHASE-2:` per-reference scoping, stated as the next tier in the meeting.

### ADR-009 - Closed-set verbalisation
Because only allowed figures can reach verbalisation (invariant 1 + the numeric guardrail), the set of figures the system can ever speak is finite and enumerable: inventory figures, computed derivations, whitelist. Spoken forms are therefore a lookup table (`data/spoken-forms.yaml`), native-verified once per language, rather than an open-ended number-to-speech engine with Arabic agreement rules. Unknown values fall back to plain digits, which TTS reads acceptably. This converts the hardest day 3 task into a reviewable data file.

### ADR-010 - Explicit language selection, not auto-detection
The buyer picks a language before the call; that sets the STT hint. A wrong auto-detection on the opening word corrupts the whole call, and Dubai buyers code-switch as normal register - an Arabic speaker opening in English is not a language signal. Mid-call code-switching is supported where the vendor allows. `PHASE-2:` on the hotline, selection happens in the first spoken turn.

### ADR-011 - Deterministic confirmation policy
Confirmation of critical entities does not depend on vendor confidence scores, which are often absent or uncalibrated on streaming STT. Policy: the first budget mention is always confirmed, including its currency; project names are confirmed when fuzzy-match score is marginal; three consecutive failed recognitions escalate. Vendor confidence, where good, tightens the policy; it is never the only trigger. Detail in `docs/04-`.

### ADR-012 - In-memory state, no database
Session state in the agent process; the CRM write is an interface with a console implementation (`STUB:`). A database adds deployment surface and a data-residency conversation we do not want during a POC. Refreshing loses the conversation; acceptable for a demo.

### ADR-013 - Disclose the AI, store no raw audio
The session opens with fixed, native-reviewed disclosure copy (never model-generated - a disclosure that varies is not a disclosure), stating the AI, the human route, and that the conversation is transcribed. The POC retains transcript, guardrail decisions, timings and the brief - no raw audio - which defers the PDPL biometric question entirely and is a good unprompted answer for their legal team. See `docs/03-`.

### ADR-014 - TTS is Fish Audio S2.1-Pro (decided 2026-08-26)

**Decision.** Text-to-speech is Fish Audio, model `s2.1-pro`, through the official `livekit-plugins-fishaudio` plugin - no custom adapter, consistent with ADR-006. `s2.1-pro-free` for development; the paid tier for the demo and anything client-facing (it carries the SLA, latency commitments and commercial licence; the free tier does not).

**Why it fits.** ~70-90ms time-to-first-audio (well inside the 75-300ms budget line), WebSocket streaming with interruption handling, 83 languages with Arabic explicitly listed, instant voice cloning (serves assumption A8's voice-sample conversation and the `PHASE-2:` named-ambassador roadmap item), and first-party integrations for both LiveKit and Pipecat, so the framework fallback path survives the vendor choice.

**Caveats, all live in the build plan:**
- **Hindi is claimed within the 83 languages but not named in Fish's own material. `VERIFY:` by ear (day 0/1)** against the lakh/crore spoken forms before relying on it; same for Gulf-Arabic voice quality (native review, day 3).
- **Phoneme control exists for English (CMU Arpabet), Chinese and Japanese only.** Arabic and Hindi pronunciation of "Binghatti" and project names must use respelling in the text - the lexicon's `respell` field, applied before synthesis.
- **Fish's speech-to-text is batch-only (single file per request, timed segments).** It is NOT usable for the live recognition path. This ADR decides TTS only; streaming STT remains an open procurement with the criteria in `docs/04-`.
- Billing is per UTF-8 byte ($15 per million as of 2026-08): Arabic is ~2 bytes per character and Devanagari ~3, and synthesis is billed on the verbalised (expanded) text. Reflected in `docs/08-`.
- **Measured, not claimed (2026-08-27, day-1 gate):** through the LiveKit plugin at low-latency mode, first audio lands at ~390ms p50 (spread 377-435ms across runs), not the ~70-90ms marketing figure. Sentence-level flushing (a flush per approved sentence) recovered ~100ms of that and is on by default. Plan the voice budget against the measured number.

**Revisit when** Arabic or Hindi voice quality fails native review - the swap is a config change plus new voice ids, which is the point of ADR-006.

### ADR-015 - STT is Qwen3-ASR-1.7B via OpenRouter, Arabic gated on day 0 (decided 2026-08-27; amended same day, superseding whisper-on-Groq)

**Decision.** Speech recognition defaults to `qwen/qwen3-asr-1.7b` through OpenRouter's transcription endpoint (`POST /api/v1/audio/transcriptions`, base64 audio in, JSON text plus usage out), one request per finalised utterance - which is exactly our turn flow (steps 1-2). Because language is selected before the call (ADR-010), **STT routes per language** via `STT_MODEL_AR`, decided by the day 0 head-to-head: `qwen3-asr-1.7b` vs `qwen3-asr-flash` vs whisper - all three servable from the same OpenRouter key, so the bake-off needs no extra accounts. This consolidates the stack: LLM and STT now share one vendor account, and Groq drops out entirely.

**Why it fits.** Qwen3-ASR-1.7B (open weights, released 2026-01, built on Qwen3-Omni) supports 30 languages including Arabic and Hindi, language identification, and word-level timestamps, and benchmarks as state-of-the-art among open ASR models. A 1.7B model is small enough that hosted per-utterance latency should sit inside the 100-300ms post-endpoint budget - measured on day 1, not assumed. Streaming partials and word confidence remain things this architecture deliberately does not depend on: transcription happens on endpoint, and the confirmation policy (ADR-011) is deterministic.

**Caveats, all live in the build plan:**
- **Gulf/Egyptian Arabic remains the highest risk (A6/R1) and is unproven for this model.** `qwen3-asr-flash` explicitly claims Arabic dialect handling but **does not list Hindi** among its 11 languages - so if Flash wins the Arabic bake-off, it takes `STT_MODEL_AR` only, and Hindi stays on 1.7b. The routing architecture absorbs this for free.
- **Code-switching (Hinglish, Arabic-English) is untested for our pairs.** The Qwen3-Omni base is strong on zh-en code-switch; ar-en and hi-en get the day 0 recordings test, never a datasheet pass.
- **LiveKit has no OpenRouter STT plugin, and OpenRouter's transcription endpoint (base64 JSON) may not be OpenAI-client compatible (multipart).** Expect a small custom STT node in the adapter layer (`src/adapter/`, not core) - a per-utterance HTTP call, tens of lines. This is transport glue, permitted by AGENTS.md; it is a day 1 line item and part of the hook gate.
- **Keyword biasing:** the Qwen3-ASR family supports free-text context biasing (a better fit for project names than whisper's prompt hack) - `VERIFY:` on day 1 whether OpenRouter's endpoint exposes it; if not, biasing is unavailable and the pronunciation-sensitive confirmation policy carries more weight.
- Per-second pricing for 1.7b on OpenRouter: `VERIFY:` day 1; sibling Flash prices at $0.000035/sec (~$0.13/hr), so the per-call ceiling is around a cent either way.

**Revisit when** day 0 recordings show a contender beating 1.7b on Arabic or Hinglish, or day 1 shows hosted latency missing the budget - either way a per-language config swap (whisper stays available on the same key as the escape hatch), not a rebuild.

### ADR-016 - LLM is Qwen 3.7 Flash, thinking disabled on the voice path (decided 2026-08-27; amended same day: access via OpenRouter)

**Decision.** The conversation model and the brief-extraction model are both Qwen 3.7 Flash, accessed as `qwen/qwen3.7-flash` through OpenRouter (`https://openrouter.ai/api/v1`) via LiveKit's OpenAI plugin with a `base_url` override - pure configuration, ADR-006 holds. One model, one key, both channels. OpenRouter lists a single provider for this model - Alibaba Cloud International - and forwards requests directly, so there is no provider-routing variance to manage, and prompt caching passes through (cache read $0.006/M, write $0.038/M, 5-minute TTL - warm within a call, re-warmed between demo calls, cost immaterial). Direct DashScope international access remains the documented fallback if the proxy hop hurts latency; the swap is the base URL and key.

**Why OpenRouter over direct Alibaba.** One less vendor signup, and the swap-if-miss fallback becomes trivial: the same key reaches every alternative fast model, so a day 1 latency miss is a one-line model-slug change instead of a new account.

**Why it fits.** Function calling, structured output, streaming and prompt caching are all supported - the four things the two-channel turn design needs. Pricing ($0.03/M input, $0.13/M output at our tier) makes the LLM a rounding error per call and makes a separate cheaper BRIEF_MODEL pointless. ~1M context is far beyond need. Multilingual coverage includes Arabic and Hindi; generation quality in both is gated by the existing day 3 native review, same as every other language claim in this project.

**The trap: reasoning is ON by default.** Thinking tokens are generated before the reply streams, which would add seconds ahead of first audio and silently destroy the voice-to-voice budget. The adapter MUST disable thinking (or set a zero/near-zero budget) on every voice-path request. This is the single most likely silent latency regression in the stack: the latency meter is the alarm, and day 1 measures TTFT with thinking off before anything else is built on top.

**Caveats:**
- OpenRouter's public stats for this model show P50 latency of 0.67s - above the 200-600ms TTFT budget line. Those stats almost certainly include thinking-ON traffic (the default), which we disable, but treat the day 1 measurement as a genuine go/no-go: measure TTFT with thinking off, cache warm, and the real inventory prompt. If it misses, first try direct DashScope, then a different model via the same OpenRouter key - both are config swaps.
- Confirm on day 1 that thinking is actually disabled **through OpenRouter** (its unified `reasoning` parameter must reach Alibaba's `enable_thinking`): check the response usage for reasoning tokens, not just the latency. A silently ignored flag is the ADR's named trap arriving through the proxy.
- **Day-1 outcome (2026-08-27/28):** thinking-off confirmed - 0 reasoning tokens on every observed call. Measured TTFT with thinking off: p50 836ms across mixed runs (min 547ms), which exceeds the 200-600ms budget line; the slow tail is attributable to upstream 429 congestion (logged separately as `llm_upstream_error` so the meter cannot misattribute it) and prompt caching not engaging. The revisit clause stands: if caching plus BYOK do not bring the clean-path p50 inside budget, the model-swap fallback applies.
- **Prompt caching, resolved by measurement (2026-08-28):** Alibaba's caching through OpenRouter is explicit-only for this model. A `cache_control: {type: ephemeral}` breakpoint on the **system content block** engages it (measured: cache write on call 1, 1580 tokens read back on calls 2+, 82% off the prompt cost, break-even on the second call, 5-minute TTL, and a suggestive but not yet established ~300-400ms TTFT improvement); the top-level `cache_control` parameter is **silently ignored** - it looks applied and does nothing - and OpenRouter's supported-models list omits this slug even though it caches. The catch: the LiveKit OpenAI plugin serialises message content as plain strings with no path to content-block `cache_control`, so `cached_tokens` is structurally 0 on the voice path. Turning it on requires either rewriting the outbound body in the transport tap (which would break that module's copy-only contract) or subclassing the plugin (which ADR-006 resists) - a deliberate decision for later, not a config flag. The latency meter already plumbs `cached_tokens`, so it will show the moment this is enabled.
- **Observability fragility:** the reasoning-token gate is read via a pass-through httpx transport tap, because the framework's usage type has no reasoning field and discards what OpenRouter returns. A framework upgrade can silently break the tap - treat the disappearance of `thinking_off` telemetry as a gate failure, never as a pass.
- Tool-call reliability while streaming is exercised by the day 1 hook gate on this exact model, not assumed.
- Digit discipline (the model must emit figures as digits, invariant 4a's dependency) is covered by the digit-emission eval category - run it against this model early.
- Data path is OpenRouter (US) forwarding to Alibaba Cloud International. Assumption A5 unchanged for the POC. `PHASE-2:` note for the meeting: a production build would go direct to the model host, and Alibaba Cloud has UAE-region infrastructure, so a UAE-resident inference story may be achievable with this vendor - `VERIFY:` whether Model Studio serves this model from the UAE region before claiming it.

**Revisit when** measured TTFT with thinking off exceeds the budget, or Arabic/Hindi generation fails native review.

## Deployment

| Component | Where | Note |
|---|---|---|
| Next.js demo UI | Vercel | UI only; no provider calls |
| Python agent worker | Railway | Long-lived process; connects out to LiveKit Cloud, so no inbound routing needed |
| Transport | LiveKit Cloud | WebRTC; handles venue-network jitter; SIP later |
| STT / TTS / LLM | Vendor APIs from the agent worker | Keys in Railway env only |

`PHASE-2:` production moves inference behind UAE-region infrastructure; nothing in the core changes - that portability is a design property, not an accident.

## Configuration

```
LLM_MODEL=qwen/qwen3.7-flash   # decided, ADR-016 (amended: via OpenRouter)
LLM_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=
LLM_THINKING=off               # NEVER on for the voice path - thinking precedes speech (ADR-016)
BRIEF_MODEL=qwen/qwen3.7-flash # same model, thinking off; separate var so it can diverge later
STT_PROVIDER=openrouter        # decided, ADR-015 (amended) - per-utterance transcription on endpoint
STT_MODEL_DEFAULT=qwen/qwen3-asr-1.7b
STT_MODEL_AR=                  # set after day 0: qwen3-asr-1.7b | qwen3-asr-flash | whisper (same key)
TTS_PROVIDER=fishaudio         # decided, ADR-014
FISH_API_KEY=
FISH_TTS_MODEL=s2.1-pro        # s2.1-pro-free for dev; paid model for the demo (SLA + commercial licence)
TTS_VOICE_ID_EN= / _AR= / _HI= # Fish voice reference ids, chosen by ear
GUARDRAIL_MODE=enforce|warn    # warn logs violations without blocking; enforce is default
PROMPT_MODE=ambassador|naive   # naive pairs with warn for the defence-in-depth demo (docs/03-)
DEMO_MODE=true|false           # seeds the scripted conversation from docs/07-
```
