# 08 - Cost model

The pitch's commercial spine is that the 80015 hotline is a cost line with fixed capacity and Gulf business hours. This page arms the meeting with the other side of that comparison. Fill the measured column from real rehearsal sessions on day 5; the planning column is order-of-magnitude only and every number is `VERIFY:` against the chosen vendors' current pricing.

## Per-call cost, assuming a 5-minute call (~10 turns, ~2.5 min agent speech)

| Component | Driver | Planning estimate | Measured (day 5) |
|---|---|---|---|
| STT (Deepgram nova-3 streaming, ADR-017) - **the shipped path** | billed per minute of audio **streamed**, so the driver is call duration, not speech duration: ~5 min. Pay-as-you-go $0.0048/min monolingual, $0.0058/min multilingual (promotional; the same page shows regular $0.0077 and $0.0092) - deepgram.com/pricing, read 2026-09-01. `VERIFY:` the rate on our own account | $0.02 - 0.05 | |
| STT (qwen3-asr-1.7b via OpenRouter, ADR-015) - the retired path, still selectable | ~10 utterance requests, billed per second; `VERIFY:` 1.7b rate (sibling Flash: $0.000035/sec, ~$0.13/hr). Not billed unless `STT_PROVIDER=openrouter` | around $0.01 | |
| LLM - conversation (Qwen 3.7 Flash, ADR-016) | ~10 turns x (3-6k input, mostly cache-hit + ~150 output) at $0.03/M in, $0.13/M out | under $0.01 even uncached | |
| LLM - brief extraction (same model) | ~10 calls, short transcripts | well under $0.01 | |
| TTS (Fish s2.1-pro, ADR-014) | ~2.5 min synthesised, $15 per 1M UTF-8 bytes | ~$0.05 English; 2-3x for Arabic/Hindi (2-3 bytes per character, and synthesis is billed on the verbalised, expanded text) | |
| Transport (LiveKit Cloud) | ~5 participant-minutes | $0.01 - 0.05 | |
| **Total per call** | | **roughly $0.07 - 0.30 as shipped; $0.05 - 0.25 on the retired STT path. Dominated by TTS either way** | |

Notes for the meeting:

- **The two STT rows are alternatives, not a sum.** `STT_PROVIDER` selects one and only one is billed. Deepgram is the component the issue-#10 conversation is about: it is the only line here that is not already on an account we hold, so it is the one number in this table nobody can look up internally. Since ADR-017 it is also the default, which means it is billed on a fresh checkout rather than opted into.
- **"Streaming charges only the tail" is a latency claim, not a billing one.** ADR-017's phrasing is right about the thing it measured - 258-327ms after endpoint against a p50 of 1081ms - and it does not describe the invoice. Deepgram bills the audio streamed, so the driver is call duration: a caller who thinks for thirty seconds is thirty seconds of billed audio, where the whole-utterance path billed roughly what was said. That is the one place the streaming decision costs more rather than less, it is worth about $0.02 a call, and it is a trade the latency numbers plainly win. Say it that way if the tech lead asks, rather than repeating the latency sentence at a billing question. `VERIFY:` whether the stream is held open while the agent speaks, which is what makes the driver the full five minutes rather than half of it.
- **Arabic sits outside nova-3's multilingual mode, and ADR-017 was measured in English.** Deepgram documents Arabic on nova-3 as pinned single languages (`ar`, `ar-AE` and other regional variants), while the code-switching "multilingual" setting covers English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch - developers.deepgram.com/docs/models-languages-overview, read 2026-09-01. So Hindi-English code-switching is a supported mode on the provider we now ship and Arabic-English is not, on the provider whose keyterm boosting we want for the client's own name. ADR-017's bake-off ran three English utterances, so nothing here has been measured in Arabic at all. Not a cost note: it is an open question against a decided provider, and it is why the Arabic slot may still end up somewhere else. `VERIFY:` by running it in Arabic, not by reading it.
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
| Deepgram account | Required as shipped (ADR-017 default), not optional. Pay-as-you-go, no commitment; the page also lists a cheaper committed Growth tier. The streaming rates above are marked promotional on Deepgram's own page, so they are not a rate to quote a client without a date on it. `VERIFY:` current rates and any free starting credit, on our account |
| OpenRouter account (LLM + STT) | Prepaid credits (~5% purchase fee); token prices pass through ($0.03/M in, $0.13/M out; cache read $0.006/M, write $0.038/M, 5-min TTL); STT billed per second; the day 0 bake-off costs cents on this same account |
