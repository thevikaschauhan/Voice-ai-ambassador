# 00 - Brief, scope and assumptions

## Client context

Binghatti Developers is a Dubai luxury developer with 35+ active residential projects and three branded lines (Bugatti Residences, Mercedes-Benz Places, Burj Binghatti Jacob&Co). They are a listed sukuk issuer, so disclosure discipline is already part of their operating culture - the compliance argument in this proposal will land, not annoy.

Their current customer-facing surfaces, and what each implies:

| Surface | Observed behaviour | Implication |
|---|---|---|
| Website catalogue | 35+ projects, filter-based search | Discovery load is high without a salesperson |
| Registration form | Ends with "our sales representative will be in touch shortly" | Unbounded latency between intent and response - the core wedge |
| WhatsApp / toll-free 80015 | Prominent on every page | Conversational channels already accepted by the brand |
| "Meet our brand ambassador" | Zoom/Teams slots, GST timezone | A booking primitive already exists to integrate with |
| Mortgage calculator | Standalone page | They have conceded buyers want self-serve financial reasoning |
| Partners portal | Separate broker channel | `PHASE-2:` broker copilot |

Market pressure (`VERIFY:` all of these figures before saying them to a client who knows the market better than the source did): off-plan is roughly three-quarters of Dubai residential transactions; H1 2026 off-plan volumes down ~7% year on year with new launches down sharply; the buyer base spans India, UK, Russia, China, Pakistan, Turkey, Egypt, France, USA, Germany - five-plus time zones, six-plus languages. Fewer, more selective buyers competing for less inventory means response latency and unanswered financial questions are directly monetisable.

## Why voice

The 80015 hotline is a real cost line with fixed capacity and Gulf business hours, serving buyers across five time zones. Text chat competes with a web form; voice competes with a missed call, which is a number their commercial team can value. `docs/08-cost-model.md` arms the meeting with the per-call economics.

Voice also raises the stakes on grounding, usefully. A wrong price in a chat window is a screenshot. A wrong price spoken in a warm, confident voice is what a buyer repeats to their spouse and quotes back to the sales team three weeks later. Say this in the meeting - it sets up the guardrail demonstration.

## The evidence exhibit

Third-party portals publish mutually inconsistent facts for the same Binghatti project. For Binghatti Skyrise alone, public sources give handover as Q4 2025, September 2026 and December 2026, and studio entry prices of AED 975,000, AED 1,020,000 and AED 1,200,000. A general-purpose model answering from the open web will confidently produce one of these wrong answers. The architecture exists to make that impossible. Lead the meeting with this. (`VERIFY:` re-pull the current portal figures the week of the meeting so the exhibit is fresh.)

## Scope

### In scope

- POC 1 (this repository): multilingual voice brand ambassador. Cascaded speech pipeline with grounded retrieval, guardrails in code, live lead brief, escalation, and meeting booking (`STUB:`). Three languages ship: English, Arabic, Hindi. The remaining three (Russian, Mandarin, French) are configuration plus native review, presented as roadmap.
- POC 2 (deferred, presented as roadmap): deterministic investment modelling engine plus AI-written, validated investor one-pager. Not in this repository until after the meeting.

### Explicitly out of scope, named in the meeting as roadmap

`PHASE-2:` SIP telephony into the 80015 hotline (the POC runs over a browser microphone; the pipeline behind it is identical, and LiveKit supports SIP natively, so this is an integration, not a rebuild - say that plainly) - live CRM write-back - WhatsApp Business API - broker/partner copilot - post-sale owner agent - voice cloning of a named ambassador - authentication and buyer accounts.

## Assumptions register

Carry this into the meeting. Presenting known unknowns is more credible than presenting none.

| # | Assumption | Risk if wrong | How to resolve |
|---|---|---|---|
| A1 | Inventory can be exposed as a JSON feed or CMS export | Rebuild the ingestion layer | Ask which system holds project data and whether it has an API |
| A2 | Published prices are "from" prices, not per-unit availability | Agent quotes a price for a sold-out configuration | Ask whether unit-level availability exists and where |
| A3 | A real price sheet is obtainable before the meeting | Demo shows placeholder figures the tech lead knows are wrong | Ask their contact for a current price sheet; failing that, label figures as illustrative on screen |
| A4 | The Zoom/Teams booking flow has an API | Booking stays a `STUB:` | Ask what powers the ambassador scheduler |
| A5 | Buyer PII may be processed outside the UAE during the POC | Requires UAE-region inference for a pilot | Raise data residency in the first meeting; see `docs/03-` |
| A6 | STT handles Gulf and Egyptian Arabic, not only MSA, at usable accuracy | The Arabic demo fails in front of an Arabic-speaking room | Test with real recordings before day 1; native speakers on day 3. Highest-risk assumption |
| A7 | Arabic-English and Hindi-English code-switching is recognised intra-sentence | Frequent confirmation loops | Vendor evaluation against real recordings, not datasheets |
| A8 | Binghatti will accept a synthetic voice representing the brand | Voice becomes a blocker | Bring two or three voice samples and let them choose |
| A9 | Call transcription consent and voice-as-biometric-data are resolvable under PDPL | Production stalls on legal review | Raise in the first meeting. The POC stores no raw audio, which defers the question |
| A10 | LiveKit's text-interception, function-tool and post-turn hooks behave as documented | The guardrail invariant has nowhere to live | Day 1 gate covers all three hooks, not just one. Switch to Pipecat immediately rather than adapting |

## What we ask Binghatti for at the end of the meeting

1. A current price sheet or read access to the inventory system (resolves A1-A3).
2. An introduction to their legal/compliance contact (A5, A9, and the Trakheesi question in `docs/03-`).
3. What powers the ambassador booking calendar (A4).
4. Agreement on a pilot scope: one project cluster, one language pair, a measurement window.
