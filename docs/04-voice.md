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

Mitigations, in order: explicit language selection (ADR-010); per-language STT routing with the Arabic slot decided on real recordings, never datasheets (ADR-015); keyterm boosting loaded with every project and area name (working on Deepgram, ADR-017; the previous path had no such mechanism); the confirmation policy below, project-name read-backs and the escalation after three failed recognitions included. The reviewer packet (`docs/review-packet-ar.md`, `docs/review-packet-hi.md`, regenerate with `uv run python tools/reviewer_packet.py ar`) is generated from the loaders themselves, so it asks for exactly what the system will demand back and cannot go stale when inventory changes. It deliberately combines the copy authoring and the recordings into one sitting: the reviewer's calendar is the long-lead dependency in the build, and two separate bookings cost a week that one 20-minute extension does not. **Test with real speakers before the demo** - an Emirati speaker, a Hindi-English code-switcher, each running the eval script, will find more in an hour than a week of desk testing. If Arabic is marginal, ship it and demonstrate graceful confirmation-and-escalation on a hard case: degrading gracefully on the hardest language reads better in a Dubai boardroom than quietly avoiding Arabic, which will be noticed.

## Buyer-side numbers and currency

The plan is as strict about input numbers as output ones. Two failure modes:

1. **Misrecognition**: "two million" transcribed as "two hundred million".
2. **Unit and currency ambiguity**: "do crore ka budget hai" - two crore of what? INR 2 crore is roughly AED 880k; AED 2 crore is 20 million. Guessing wrong recommends a property off by up to 20x. Same trap with "million" from European and Russian buyers assuming home currency.

**Confirmation policy (ADR-011), deterministic - the budget half implemented in `ambassador/budget.py`, not in the prompt.** It was prompt constraint 8 until this landed, and ADR-007 is explicit that prompt instructions reduce violation rates without eliminating them. What makes it deterministic is that the policy takes the turn: when a confirmation is owed, `llm_node` speaks it and the model never runs, so the question cannot be skipped, reworded, or answered on the buyer's behalf.

The first budget mention is always confirmed - an unstated currency gets "2 crore - is that in dirhams or in rupees?", a stated one gets a read-back to catch a misheard number. Currency words and budget keywords are owned by their nearest figure and never reach across a sentence or clause break, so the deposit's "AED" cannot resolve the crore budget and a quoted price cannot steal the word "budget" from the buyer's own number. While the question is open, every reply is read for five things, in order: a restated budget (which replaces the stale mention and restarts the confirmation), a contradiction ("no", "that's wrong", "I'm not sure") - read before any currency in the same reply, so "no, dirhams" rejects the read-back instead of settling it - a currency named without negation, a currency denied ("not dirhams" names rupees, because there are exactly two), and explicit agreement ("yes", "correct"). Consent is never inferred: "can you repeat that?" carries no signal and is a failed attempt, not a yes. Three replies that answer nothing hand the buyer over. The word lists driving negation, contradiction and agreement live in `data/currencies.yaml`, per language.

Both terminal actions ("cannot convert", "give up") actually notify a human through the same routing as the `escalate_to_human` tool, not just say so - a spoken "let me put you through" with nobody notified is the anti-pattern the tool's own docstring names.

Three things are deliberately refused rather than approximated:

- **No conversion on an unverified rate.** `data/currencies.yaml` ships no rate and `confirmed: false`. A made-up exchange rate spoken to a buyer is the same class of error as a made-up price - a specific, checkable, wrong number said with confidence - so a non-AED budget is handed to a human instead. An operator sets a real rate, dates it, and flips the flag. Worth demonstrating rather than hiding.
- **No English confirmation in a non-English call.** The copy is per language and only English is authored, so the policy reports itself off for ar/hi rather than asking the question in the wrong language. For exactly those languages, constraint 8 keeps its original wording - the model is asked to confirm budgets itself - because telling the model the system owns a question the system will not ask leaves nobody asking.
- **No silent fall-through on failure.** A confirmation that cannot be composed - a broken template, an echo the transcript check refuses - fails CLOSED: the agent speaks the give-up line and escalates. The first version returned the turn to the model on error, which read as fail-safe and was fail-open.

The confirmation echoes the buyer's own budget through a separate, bounded path rather than `SentenceGuard.compose()`: `process_sentence` is guardrails and then verbalisation, and verbalising the echo would assert a currency the buyer never named on the very turn that exists to ask which they meant. The template comes from `data/confirmations.yaml`, the slot must be a literal substring of the utterance the mention was extracted from (not of the current turn - a re-ask happens precisely because the reply did not repeat the number), and no model output passes through it. The echo therefore reaches TTS as the transcript surface, digits included - the same plain-digits fallback ADR-009 accepts for anything outside the spoken-forms table. The spoken text stays out of the emitted event stream for the same reason a buyer utterance does; the settled currency is emitted as `budget_settled`, named apart from the brief extractor's model-inferred `budget.confirmed` field on purpose.

**Project names are confirmed when the fuzzy match against inventory is marginal**, implemented in `ambassador/projects.py` on the same seam. This is not a hypothetical: every recogniser in ADR-015's bake-off mangled the client's own name ("Bint Jbeil", "Binghati", "binghati") and OpenRouter's transcription endpoint ignores the biasing parameter, so nothing fixes it on the input side - while `Binghatti Skyrise` and `Binghatti Aquarise` differ by one syllable and cost different amounts. Matching is derived from `data/inventory.json` and nothing else, because an alias table would be a second source of project names and invariant 1 allows one: per project, the full name, the tokens unique to it, and each unique token alone, each scored against the utterance with `difflib` (standard library, no new dependency). Below a floor no name was said and the model answers; at or above a high threshold WITH a clear margin over the runner-up the match is confident and nothing is read back, because a read-back nobody needs is how a policy gets switched off; in between it is marginal and the policy asks "just to be sure - did you mean Binghatti Skyrise?".

The index also holds decoy keys - every area name, its tokens, and any token shared by more than one project (`binghatti` itself) - and a decoy that is a credible match explaining at least as much of the utterance means no project was named. Without them "Jumeirah Village Circle" (the area `Binghatti Circle` sits in) reads back as the project and "tell me about Binghatti" reads back as whichever name scores nearest. Two details are load-bearing and both were found by breaking them. The comparison is on how much of the utterance a key explains, not on how similar it is: "Binghatti Skyrize" contains `binghatti` exactly, so a similarity comparison suppresses the one match the trigger exists for. And a decoy may only suppress a match it COMPETES with - one whose words overlap - because comparing across the whole utterance let an exact area beat a fuzzy project phrase sitting beside it: "Binghatti Skyrize in Business Bay" was suppressed by the area and the model answered a mangled name unconfirmed, defeating the trigger in precisely the situation it exists for. A decoy that explains other words explains nothing about these ones.

Two calibration rules earn their own mention, because ordinary language crosses a naive threshold easily. A ONE-WORD key needs a higher score than a multi-word one, since a single word has no context to be wrong about: "arise" scores 0.769 against "aquarise" and asked a buyer discussing prices about a tower they never mentioned. And a multi-word key needs its evidence DISTRIBUTED - at least two of its tokens matching - because a mean lets one strong token carry a weak key: `residences` appears verbatim in "what residences are available?", and one exact match plus two poor ones averaged over the floor. Only the leading distinctive token of a name stands alone as a short form, since buyers shorten a name to its head ("the Bugatti", "Skyrise") and never to its trailing descriptor. A recogniser that splits a name instead of mangling it is caught by rejoining adjacent tokens ("sky rise" reads as "skyrise"), and a match that needed rejoining is never treated as confident - the recogniser is demonstrably guessing at word boundaries in that utterance.

Unlike the budget, a settled project does not close the policy - a call moves from one tower to another - but a name the buyer has confirmed is never read back again and a name they have rejected is never offered again. Three answerless replies hand over. The slot here is bound to INVENTORY rather than to the transcript, which is the mirror image of the budget echo's bound and for the mirror-image reason: verbalising a buyer's amount would assert a currency they never named, while reading a buyer's mangled words back would confirm our mishearing rather than the project. Both slots are closed sets no model output can reach, and because this line carries no figure it goes through `SentenceGuard.compose()` like the fallback copy rather than round it.

**Three consecutive failed recognitions escalate warmly** (`ambassador/recognition.py`) - a separate count from the budget policy's three attempts, which counts replies that answered wrongly. A failed recognition is deterministic and has two shapes: a transcript with no letter or digit in any script (nothing, whitespace, the punctuation a recogniser emits around silence), or one whose every token is a filler from `data/recognition.yaml`. The empty half is language-neutral and live in all three languages; the filler half needs an authored list and a language without one never calls a turn garbage. Anything the buyer actually said - "what?", "no", "sorry" - is a recognition and gets answered. The count resets on any real turn, because three failures spread over a good call are three ordinary "could you repeat that" moments, and the escalation is spoken once rather than on every crackle.

**Which policy owns a turn is one decision, in one place** - `ambassador/confirmation.py`, pure core, shared with the eval harness rather than reimplemented beside it. Ordering alone was not enough, and an independent review reproduced why: with a project question open, "Yes, and my budget is 2 crore" had its *Yes* discarded by the budget's precedence, and the "Dirhams" that answered the budget was then read by the project policy as a failed attempt - the buyer answered both questions correctly and was handed to a human two turns later. The two failures want opposite orders, so the rule is ownership, not order:

- **A reply belongs to the question it answers.** The owner is the policy whose question was asked most RECENTLY and is still open, which is the question a person would take an answer to be about. It reads the turn only if the reply actually says something about it (a pure predicate on each policy, `answers()`). Any other open question is SUSPENDED: it is not read, so it cannot be answered by accident and cannot lose an attempt.
- **Then fresh mentions**, from policies with nothing open, budget first. This is why the same reply settles the name AND opens the currency question: the answer is honoured, and the new mention still gets asked about. It is also why a budget stated while answering a name question is never lost - losing it would leave the model acting on an unconfirmed figure, which is the twenty-times risk arriving by the back door.
- **A reply nobody claims answered nothing**, and is a failed attempt on the owner. Consent is never inferred from it.
- **A suspended question is still owed** and is asked again once its turn comes, having consumed nothing. The model never takes a turn while a confirmation is open.

**Any handover quiesces every policy.** A buyer told "let me bring in one of our ambassadors" and then asked, two turns later, about a budget from earlier in the call has not been handed over, they have been paused - and that was the behaviour before the review. Quiescing is by construction (every policy is abandoned) rather than by a guard at each read site, because a guard is one site away from being forgotten. The model's own `escalate_to_human` deliberately does NOT quiesce: it fires routinely on an unknown project or a branded price, and its own docstring tells the model to keep speaking, so treating it as terminal would silence the confirmations on an ordinary call.

**Every slot-free terminal line is composed through the guardrails once, at construction.** These are the lines spoken verbatim on a failure path, and the first version composed them per turn and caught a rejecting guard by speaking the raw string - a literal bypass of the single public speech path, on the one path that cannot afford one. Choosing a direction at runtime was the wrong question: copy that fails our own guardrails is a defect in the copy, so it is checked while the operator is still looking at a terminal, and a language whose own handover line fails its own guardrails refuses to start - the same rule as an unauthored disclosure blocking its language.

Still ADR-011, not yet implemented:

- Vendor word-confidence, where available and sane, tightens these triggers; it is never the sole mechanism, because streaming confidence is often absent or uncalibrated.

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
