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
| Named ambassadors | Each language's ambassador has a given name, in `data/ambassadors.yaml`: Jane in English, Nora in Arabic, Maya in Hindi, all chosen by the client. Three named ambassadors rather than one rendered three ways. The name is product identity rather than language copy, so choosing it is not the build team authoring in a language it does not speak - but HOW each is written in Arabic or Devanagari and said aloud is a native-reviewer question, still open, asked in reviewer packet 4b. Until answered, the client's own Latin spelling is what the agent speaks and the surface labels |
| Per-call language | The room carries its language in room metadata and the entrypoint reads it, falling back to `LANGUAGE`. Three languages from one worker, instead of one worker per language (`task-hosted-language-from-metadata`) |
| Hosted transcript source | The framework's own `lk.transcription` text streams, which carry the buyer-visible words and nothing else. No event bridge on the hosted service (`task-hosted-language-from-metadata` verifies it) |
| Hosted access controls | A public URL in front of paid providers, so: an access code checked server-side, a cap on concurrent demo rooms, a short token TTL, short room timeouts, and a per-call duration cap. Named in `docs/09-deploy.md` |
| Graceful call ending | The agent detects that the buyer is closing the conversation, speaks an authored farewell in the call's language, and ends the job so the room closes and the browser sees the disconnect. Asked for by the client after their first hosted call: today nothing ends a call but the buyer closing the tab, so a client who says goodbye hears silence (`task-graceful-goodbye`) |
| Voice-agent talk surface | Once a call starts, `/talk` is an orb and its subtitles: a dark disc with a coloured corona that breathes when listening, blooms with the ambassador's voice, and shows distinct states for the visitor speaking and for thinking. The transcript reads as subtitles under it - the current utterance large and centred, the previous lines fading above - built on the segment-keyed rail. The ambassador has a name from `data/ambassadors.yaml`, English **Jane**, and it labels the orb and her lines. `prefers-reduced-motion` gets a static glow whose state is carried by colour and label instead of movement (`task-talk-orb-experience`) |

### Faked (`STUB:`)

Booking = spoken read-back, no calendar. CRM write = console log behind an
interface **in Phase 1 only; P2-S02 replaces it with the durable lead record**.
Inventory = hand-authored file, not a feed.

### Deferred (do not build; present as roadmap)

Remaining three languages - SIP/80015 - WhatsApp follow-up -
per-referenced-project inventory scoping - `compute_payment` tool - POC 2.
The durable event store, encrypted/hashed PII, admin leads and knowledge corpus
are no longer deferred: the Phase 2 contract below promotes them deliberately.
Other older `PHASE-2:` markers remain roadmap unless a row below names them.

On the hosted client demo specifically, three things are deliberately absent rather than unfinished. **The latency meter, the guardrail and violation panels, and the ambassador brief stay laptop-only**: they carry the unredacted records that issue #30 keeps loopback-bound, and they are the tech lead's screen in the meeting rather than the client's. **Hosted text mode** stays laptop-only for the same reason it exists, being a fallback for a room with bad audio; on the hosted service it refuses with a reason instead of serving a script. **A transport for the event bridge between services** is not built, because the bridge's loopback restriction is a security property and replacing it is design work this POC does not need once the transcript comes from the framework. The hosted page says which panels it is not showing, in one sentence.

On hosting specifically: one Railway project with one environment, so no staging tier. No custom domain (the generated Railway domain is the demo URL), no autoscaling, and no replica count above one. The web gates are no longer on that list: `npm test`, `npm run typecheck`, `npm run lint` and `npm run build` run as a third job in `gates.yml`. All of these are consequences of the two-service topology rather than separate choices; `docs/09-deploy.md` is where they are argued.

## Phase 2 - admin leads and knowledge base

This phase begins after the working voice POC. Its architecture is ADR-018
through ADR-021 and its surface contract is `docs/10-admin.md`. The human has
approved Supabase Postgres, one shared admin code, one declinable contact ask,
the per-figure document gate, the four ingestion formats, and the explainable
score. The tables below are therefore decisions, not a menu.

### Phase 2 ships

| ID | Item | Note |
|---|---|---|
| P2-S01 | Supabase Postgres and migrations | Frankfurt free project; portable plain Postgres through the IPv4 session pooler; small explicit asyncpg pools; versioned SQL under `agent/`; no Supabase SDK/Auth/Storage/RLS |
| P2-S02 | Durable lead snapshot for every call | Project full-fidelity turns and last accepted brief after brief drain; idempotent on session id; `buyer_left` and incomplete calls included |
| P2-S03 | Structured summary and interest score | Pydantic model output with evidence turns; score arithmetic and versioned generic weights in pure code; analysis failure keeps the lead |
| P2-S04 | Manual qualify/reject with audit | Score is guidance; append-only decisions update status transactionally with optimistic revision checks |
| P2-S05 | One-time contact capture | Name plus phone or email, declinable; first goodbye may be intercepted once; second goodbye closes; phone digits are read back deterministically |
| P2-S06 | Knowledge paste and upload | Synchronous bounded parsing for PDF, DOCX, TXT and pasted text; scanned PDFs fail visibly as no extractable text |
| P2-S07 | Deterministic chunks, four-way scope review and per-figure review | Paragraph-aware chunks default admin-only; bound `project_knowledge` descriptive prose may retrieve only for an inventory project; conflicts/unknown projects stay closed; structured inventory facts remain `inventory_governed`; extracted occurrence list shows value, unit/currency, source sentence and page; only individually approved figures in eligible chunks are active |
| P2-S08 | Full-text retrieval in the voice path | Published `general_knowledge` and bound `project_knowledge` chunks; known project chunks rank first, general knowledge always eligible; at most four chunks; once per final turn, cached across repeat `llm_node` calls, at most 250ms before `llm_ttft` |
| P2-S09 | Retrieved-figure guardrail extension | Approved figures from retrieved chunks extend that turn's set; withheld/revoked values are removed before prompting; no validator bypass |
| P2-S10 | Python admin API | Private FastAPI service from the same Python image; bearer-protected lead, decision and knowledge routes; only health is unauthenticated |
| P2-S11 | Protected `/admin` web surface | Shared code, unset-closed and rate-limited; signed HttpOnly session; fixed server proxy routes add the bearer; lead/detail/decision and knowledge-review UI |
| P2-S12 | Durable audit and PII protection | Existing clear-event classification persists; buyer payloads use authenticated encryption; phone/email use a separate keyed fingerprint; no buyer words on stdout |
| P2-S13 | Deployed failure, pause and restart behavior | Railway hosts web, worker and private admin API; Supabase hosts Postgres; daily keep-active query plus pre-demo one-click-restore check in `docs/09-`; database pause/outage never blocks a call; saved records and approvals survive restarts |

### Phase 2 faked (`STUB:`)

| Item | Honest boundary |
|---|---|
| CRM integration | The admin database is the lead system of record. No Salesforce/HubSpot write or salesperson notification |
| Actor identity | Shared-code decisions record actor `admin`, not a named employee |
| Delivery guarantee | A failed database write emits a clear failure event. There is no local durable spool or at-least-once queue |
| Free-tier availability | One scheduled low-cost query each day mitigates the roughly seven-day inactivity pause; it is not a production uptime commitment or substitute for the pre-demo state check. Published 500 MB database cap is `VERIFY:` in the creation dashboard |
| Retention automation | The schema carries `retention_expires_at` and an audited delete route. The legal period is still `VERIFY:` and no scheduled purge runs yet |

### Phase 2 deferred

| Item | Trigger to revisit |
|---|---|
| Per-user login, roles and named actor attribution | More than the approved POC shared code or more than one admin role |
| Multi-tenant knowledge and tenant-specific keys | A second merchant/developer enters the deployment |
| Embeddings/vector search | Measured full-text misses or corpus growth beyond the approved 10-15 documents |
| OCR/scanned PDFs, images, legacy DOC, XLSX and URL ingestion | A real source arrives in one of those formats |
| Supabase Storage or another object store | Original upload retention or corpus size makes extracted-text-only storage insufficient |
| Background ingestion workers and a job queue | Synchronous parse exceeds route limits at measured document sizes |
| Local persistence spool and at-least-once delivery | Lead-loss tolerance becomes stricter than the POC's loud failure |
| Automatic qualification/rejection | Never without a separate human decision, policy and audit ADR |
| CRM sync, notifications and outbound follow-up | Binghatti names the target system and consent flow |

### Phase 2 TDD contract

Every implementation card lands as a RED commit containing a test that
compiles, runs and fails for the intended missing behavior; a GREEN commit with
the smallest implementation; and an optional refactor commit. The task branch
keeps those commits unsquashed until god merges, and the PR description names
all three. A test written after the implementation does not satisfy this wave. One
mechanical rider for the web tier: Vite resolves a **literal** dynamic import at
transform time, so a vitest file that imports a component which does not exist
yet fails to LOAD and reports `no tests` - a RED commit with nothing for the
gate to count. Import through a variable specifier with `@vite-ignore` so each
case fails on its own. The Python equivalent is an import inside
each test rather than at module level, for the same reason. And a rider about
what to cover first: **test the DEFAULT branch before the interesting ones.** It
is the branch every user reaches and the one least likely to feel worth a test,
and it is where two Phase 2 cards had a defect that only a rendered page or a
container found.

Every ships row has one first RED test:

| Ships ID | Layer | RED test and intended failure |
|---|---|---|
| P2-S01 | pytest adapter | `test_migrations_create_and_round_trip_the_phase_2_schema` fails against an empty temporary Postgres database because no migration exists |
| P2-S02 | pytest adapter | `test_buyer_left_persists_after_brief_drain_with_incomplete_audit_flag` fails because `shutdown_session` has no repository hook |
| P2-S03 | pytest core | `test_every_rubric_signal_contributes_only_its_documented_points` fails because the rubric loader and scorer do not exist |
| P2-S04 | route test | `test_a_decision_appends_history_and_rejects_a_stale_lead_revision` fails because no decision route or transaction exists |
| P2-S05 | pytest adapter | `test_first_goodbye_asks_once_second_goodbye_closes_and_decline_is_valid` fails because the contact policy does not exist |
| P2-S06 | route test | `test_pdf_docx_txt_and_paste_parse_while_a_scanned_pdf_reports_no_text` fails because the ingestion route and adapters do not exist |
| P2-S07 | pytest core | `test_chunks_default_closed_and_inventory_governed_facts_and_unbound_project_prose_never_enter_prompt_context` fails because the chunk/scope builder does not exist |
| P2-S08 | pytest adapter | `test_retrieval_runs_once_per_final_turn_and_is_reused_by_repeat_llm_nodes` fails because the model path has no retrieval seam |
| P2-S09 | pytest core | `test_only_approved_figures_from_retrieved_chunks_extend_the_turn_set` fails because the dynamic figure context does not exist |
| P2-S10 | route test | `test_every_non_health_admin_route_refuses_a_missing_or_wrong_bearer` fails before FastAPI is mounted |
| P2-S11 | vitest | `admin stays closed when ADMIN_ACCESS_CODE is absent and never serialises the upstream token` fails before the admin gate and proxy exist |
| P2-S12 | pytest adapter | `test_buyer_payloads_encrypt_while_phase_2_events_contain_no_buyer_words` fails because the encryption and classified projections do not exist |
| P2-S13 | live smoke | `disconnect_upload_retrieve_revoke_decide_survive_restart` fails on the undeployed two-host topology; the smoke includes the daily probe and paused-project restore check |

### Phase 2 gates

- Core contracts, score arithmetic, chunking and dynamic allowed figures have
  100% branch coverage and no framework, database or FastAPI import.
- `cd agent && uv run pytest && uv run ruff check . && uv run ruff format --check .`
  passes with the Postgres integration fixture included.
- `cd web && npm test && npm run typecheck && npm run lint && npm run build`
  passes; `/admin` is keyboard-complete, has visible focus and works at 375px.
- All four parsers reject wrong MIME/extension, oversize and malformed input;
  scans report `no_extractable_text` and never publish an empty document.
- A prompt-injection document cannot change persona, tools, guardrail mode or
  make a withheld figure speak.
- Approved figure, revocation, wrong-chunk figure and unapproved figure cases
  pass through the real `process_sentence()` order.
- Retrieval adds no more than 250ms p50 at the approved corpus size and occurs
  once per buyer turn, not once per `llm_node` invocation.
- English contact capture passes live with phone read-back. Arabic and Hindi
  remain disabled until their reviewer-packet copy and digit sequence are
  native-approved; no English fallback asks for PII in those calls.
- A paused/unreachable database completes the voice call and emits
  `lead_persist_failed` without buyer words or exception text.
- Live smoke proves a disconnect lead, knowledge revision, figure approval and
  admin decision remain after worker and admin API restarts.

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
