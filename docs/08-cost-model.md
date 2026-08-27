# 08 - Cost model

The pitch's commercial spine is that the 80015 hotline is a cost line with fixed capacity and Gulf business hours. This page arms the meeting with the other side of that comparison. Fill the measured column from real rehearsal sessions on day 5; the planning column is order-of-magnitude only and every number is `VERIFY:` against the chosen vendors' current pricing.

## Per-call cost, assuming a 5-minute call (~10 turns, ~2.5 min agent speech)

| Component | Driver | Planning estimate | Measured (day 5) |
|---|---|---|---|
| STT (qwen3-asr-1.7b via OpenRouter, ADR-015) | ~10 utterance requests, billed per second; `VERIFY:` 1.7b rate (sibling Flash: $0.000035/sec, ~$0.13/hr) | around $0.01 | |
| LLM - conversation (Qwen 3.7 Flash, ADR-016) | ~10 turns x (3-6k input, mostly cache-hit + ~150 output) at $0.03/M in, $0.13/M out | under $0.01 even uncached | |
| LLM - brief extraction (same model) | ~10 calls, short transcripts | well under $0.01 | |
| TTS (Fish s2.1-pro, ADR-014) | ~2.5 min synthesised, $15 per 1M UTF-8 bytes | ~$0.05 English; 2-3x for Arabic/Hindi (2-3 bytes per character, and synthesis is billed on the verbalised, expanded text) | |
| Transport (LiveKit Cloud) | ~5 participant-minutes | $0.01 - 0.05 | |
| **Total per call** | | **roughly $0.05 - 0.25, dominated by TTS** | |

Notes for the meeting:

- Prompt caching matters twice: it cuts the dominant LLM input cost by an order of magnitude and reduces time-to-first-token variance. The inventory block is the cached prefix.
- TTS is decided (Fish, ADR-014) and cheap; note the free tier (`s2.1-pro-free`) carries no SLA or commercial licence, so the demo and anything client-facing runs on the paid tier. Still swappable per ADR-006 without touching anything we wrote.
- The comparison line: a staffed toll-free minute (agent salary, telephony, management, after-hours coverage) is typically an order of magnitude above the top of this range, and the AI line answers at 2am in the buyer's language with zero queue. `VERIFY:` ask Binghatti what a hotline minute actually costs them - it is a better number coming from them.

## What this is not

Not a production TCO: no UAE-region inference premium, no observability stack, no SIP trunk minutes, no support rota. Present it as the marginal cost of answering one more buyer, which is the number that matters for the "missed call at 2am" argument.

## Fixed costs during the POC

| Item | Note |
|---|---|
| LiveKit Cloud | Free tier likely sufficient for a demo; `VERIFY:` current limits |
| Railway worker | Single small service |
| Vercel | Existing plan |
| Fish Audio account | `s2.1-pro-free` for development; paid tier for the demo. `VERIFY:` current plan pricing |
| OpenRouter account (LLM + STT) | Prepaid credits (~5% purchase fee); token prices pass through ($0.03/M in, $0.13/M out; cache read $0.006/M, write $0.038/M, 5-min TTL); STT billed per second; the day 0 bake-off costs cents on this same account |
