# Operating rules for coding agents

This file is canonical for every coding agent working in this repository, whatever the harness. `CLAUDE.md` points here. If the two ever conflict, this file wins.

You are building a client-facing demonstration that will be scrutinised by an experienced technical lead at a Dubai property developer. He will try to break it. Assume every shortcut you take is the one he finds.

## Read before you write

1. This file.
2. `docs/06-build-plan.md` - authoritative on what is in scope, faked, or deferred this week.
3. `docs/01-architecture.md` - module boundaries and the ADRs.
4. `docs/02-data-contracts.md` - before writing any type or schema, check whether it already exists there.
5. `docs/03-guardrails.md` and `docs/04-voice.md` for anything touching validation or audio.

Do not start coding from the README alone.

## The five invariants

Violating any of these is a defect regardless of whether tests pass.

**1. The model never sources facts.**
Project names, prices, unit sizes, handover dates, payment structures and amenities come from `data/inventory.json` only, loaded and validated through `agent/src/ambassador/inventory.py`. If a buyer asks about something not in inventory, the correct behaviour is to say so and offer a human. Never let the model fill a gap from training data.

**2. The model never does arithmetic.**
Down payments, instalment amounts and any other derived figure are computed by pure functions in `inventory.py` at load time and serialised into the prompt alongside the source figures. Derived figures are computed, never hand-authored - a hand-typed derived number is exactly the class of error this system exists to prevent. If you catch yourself asking the model to calculate, stop. If a buyer asks a question that needs a computation the system has not pre-computed, the designed answer is a refusal plus escalation, not model maths.

**3. Every model output is validated.**
Structured payloads parse through Pydantic. Spoken sentences pass through `guardrails/pipeline.process_sentence()`. Unparseable output triggers one repair retry, then a composed fallback. Never render or synthesise unvalidated model output.

**4. Guardrails are code, and ordering is enforced by types.**
Guardrails inspect digits; verbalisation destroys digits; audio cannot be retracted. Therefore: guardrails, then verbalisation, then synthesis. The pipeline enforces this structurally - `verbalise()` accepts only a `ValidatedSentence`, and the TTS adapter accepts only a `SpeakableText`. There is exactly one public function that produces speakable text: `process_sentence()`. Do not add another path, and do not weaken the types. A test asserts the ordering; do not delete it.

**5. Escalation is a feature.**
`escalate` renders a designed state and composed speech, not an error. The demo deliberately shows the agent refusing to guess. Treat that path with the same polish as the happy path.

## Stack

- **Agent**: Python 3.12+, managed with `uv`. LiveKit Agents is the voice framework (ADR-005; Pipecat is the documented fallback). Do not hand-build VAD, endpointing, barge-in, cancellation or transport.
- **Core**: `agent/src/ambassador/` is pure Python with zero framework imports (ADR-002). Pydantic for validation, pytest for tests. The core must remain runnable and testable with no voice stack installed.
- **Web**: Next.js 15 + React 19 + Tailwind, demo surface only. The UI makes no model calls.
- **Data**: language-neutral files in `data/` (JSON and YAML), consumed by the Python core and reviewable by non-engineers.
- No database. In-memory session state. `STUB:` the CRM write behind an interface.

## Hard rules

- **Never call an LLM, STT or TTS provider from the browser.** All provider calls happen server side: in the Python agent for the voice path, in Next.js route handlers if the web tier ever needs one. API keys live in server-side env only.
- **Never hardcode a secret**, including in test fixtures.
- **Never invent inventory.** Tests use `data/inventory.json` records or fixtures in `agent/tests/fixtures/`, never new entries in the production file.
- **Never resolve a `VERIFY:` marker on your own authority.** Leave the marker and surface it in your summary.
- **Never write guaranteed-return language** into prompts, UI copy, sample content or fixtures. Not "guaranteed 8% yield", not "assured returns", not "risk-free". Regulatory, not stylistic.
- **Never let a turn end in silence.** Every failure path in the voice session resolves to composed, localised speech. A caller who hears nothing hangs up.
- **Never write verbalisation, disclosure or prohibited-pattern copy for a language you do not speak.** Mark it `VERIFY:` for native review. English is the only language the build team self-certifies.
- **Do not add dependencies** without noting the reason in your summary. Every package is a question the tech lead may ask.
- **Do not build what the framework provides.** VAD, endpointing, barge-in, cancellation, transport, reconnection: LiveKit's job.
- **Do not build anything marked deferred in `docs/06-build-plan.md`.** If a task seems to require it, stop and say so rather than expanding scope.
- **Provider swappability for the voice path is framework plugin configuration**, not a hand-rolled abstraction. Do not write a custom LLM/STT/TTS adapter layer inside the agent; select providers in config (ADR-006).

## Coding conventions

- Pure functions in the core: no I/O in guardrails or verbalisation, no env access outside config loading. This is what makes "the model never does maths" provable.
- Types once: Pydantic models in `schemas.py` are the source of truth. Do not hand-write a parallel type.
- Python: type hints everywhere, `pytest` for tests, no `Any` where a real type exists.
- Web (when built): named exports, server components by default, `'use client'` only where interactivity requires it.
- Copy is sentence case. No exclamation marks anywhere in the product voice. No emoji. Binghatti sells restraint.

## Definition of done for any task

- [ ] Types come from `schemas.py` / `docs/02-data-contracts.md`, not invented in place
- [ ] Pure logic covered by unit tests; guardrail and derivation code requires 100% branch coverage
- [ ] Any new model interaction has at least one entry in the eval set (`docs/05-evals.md`)
- [ ] No new `VERIFY:` marker silently resolved
- [ ] `cd agent && uv run pytest` clean
- [ ] Web work: reduced motion respected, keyboard focus visible, works at 375px width

## What "good" looks like for this client

Restrained. Binghatti's brand is architectural precision, not exuberance. Monochrome base, one metallic accent used sparingly, generous whitespace, no gradients, no rounded pill buttons. If a screen looks like a generic SaaS dashboard, it is wrong.

## Project learnings

Append corrections and non-obvious project facts here as they are learned. Newest first.

- 2026-08-28: prompt caching for qwen3.7-flash via OpenRouter is EXPLICIT-ONLY and the earlier "passes through" learning is misleading without this: only a `cache_control` breakpoint on the system content block engages it; the top-level parameter is silently ignored (the trap - it looks applied); the LiveKit plugin cannot emit content blocks, so `cached_tokens` is structurally 0 on the voice path until a deliberate change (transport rewrite or plugin subclass - see ADR-016). Also: when adding an escalation behaviour to the prompt, name the tool in the constraint's LEADING imperative - a tool named at the end of a constraint measured 0/3 live, the leading position 3/3. Constraint 4 (branded price enquiry) still does not name the tool and is untested for the same defect.

- 2026-08-27 (day-1 smoke, spikes/day1_smoke.py): three vendor gotchas found live. (1) Fish has TWO wallets - platform credit and API credit are separate; a funded platform account still 402s the API until fish.audio/app/developers is topped up. (2) OpenRouter audio (STT) requires a minimum $0.50 credit balance regardless of request size. (3) `qwen/qwen3.7-flash` hits upstream 429s at Alibaba under shared-pool congestion - the adapter needs retry-with-backoff, and for demo day consider OpenRouter BYOK (own Alibaba key) to escape the shared pool. ADR-016 gate PASSED: thinking-off confirmed through the proxy (0 reasoning tokens), warm TTFT 489ms, grounded reply quoting the correct inventory figure.
- 2026-08-27 (latest): ADR-015 amended - STT is now `qwen/qwen3-asr-1.7b` via OpenRouter's transcription endpoint (`POST /api/v1/audio/transcriptions`, base64 JSON - NOT OpenAI multipart, so a small custom STT node lives in `src/adapter/`, never in core). Groq is OUT of the stack; whisper remains reachable on the same OpenRouter key as the escape hatch. `qwen3-asr-flash` competes for the Arabic slot only (it has no Hindi). Day 1: verify context biasing is exposed through OpenRouter; measure per-utterance latency against the 100-300ms line.
- 2026-08-27 (later): ADR-016 amended - Qwen 3.7 Flash is accessed via OpenRouter (`qwen/qwen3.7-flash`, base_url openrouter.ai/api/v1, `OPENROUTER_API_KEY`), not direct DashScope. Single provider behind the slug (Alibaba intl), prompt caching passes through (5-min TTL). Day 1 must verify thinking-off actually reaches the model through the proxy (check reasoning tokens in usage) AND beat the 200-600ms TTFT line - the slug's public P50 is 0.67s (likely thinking-on traffic). Fallbacks in order: direct DashScope, another slug on the same key.
- 2026-08-27: LLM decided - Qwen 3.7 Flash via the DashScope international OpenAI-compatible endpoint through LiveKit's OpenAI plugin (ADR-016); same model for conversation and brief extraction. **Reasoning is ON by default for this model: every voice-path request must disable thinking, or thinking tokens run before speech and silently add seconds of latency.** TTFT is a day-1 measurement, not a datasheet fact; the fallback is a config swap to any fast OpenAI-compatible model.
- 2026-08-27: STT decided - whisper-large-v3-turbo on Groq via the LiveKit Groq plugin, per-utterance transcription on endpoint (ADR-015). STT routes PER LANGUAGE: en/hi on turbo, `STT_MODEL_AR` set by the day 0 head-to-head (turbo vs whisper-large-v3 vs a dialect specialist). Whisper has no streaming partials, no word confidence (the deterministic confirmation policy absorbs this) and no keyword boosting (use the prompt-bias parameter with project names). Never present whisper as streaming STT.
- 2026-08-26: TTS vendor decided - Fish Audio `s2.1-pro` via `livekit-plugins-fishaudio` (ADR-014). Fish's speech-to-text is BATCH-ONLY: never wire it into the live recognition path; streaming STT is a separate, still-open vendor decision. Fish phoneme control covers English only (CMU Arpabet) - Arabic/Hindi pronunciation uses respelling. The free tier (`s2.1-pro-free`) has no SLA or commercial licence; client-facing runs use the paid tier.
