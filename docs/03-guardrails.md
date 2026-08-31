# 03 - Guardrails, failure handling, compliance

This document answers the two questions the tech lead will actually ask: how do I know it will not make something up, and what happens when legal sees this. Have it open in the meeting.

## The claim, precisely

> The system cannot speak a figure that is not in your inventory, because a deterministic validator checks every number in every sentence against your records before that sentence is synthesised into audio.

The word "before" carries the claim, and in voice it is physics, not UX: played audio cannot be recalled. That is why the pipeline is cascaded (ADR-001) and why ordering is enforced by types (ADR "ordering" section in `docs/01-`).

## Validator 1 - numeric claims

`guardrails/numeric_claims.py`

**Extraction.** Every currency amount, bare numeral, percentage, year, and quarter reference. Western digits, Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩, thousands separator ٬ and decimal ٫), and Devanagari digits (०-९). A validator that misses Arabic-Indic digits is worse than useless in an Arabic reply.

Two rules about where a match may begin are load-bearing, and both were learned from a live bypass: extraction never starts **inside** a number (after a digit, comma or decimal point), and an adjacent **letter never blocks it**. With letters blocked, `AED750,000` extracted nothing at all and went unchecked; worse, `AED1,985,000` restarted after the comma, extracted the embedded and genuinely allowed `985,000`, and so validated a fabricated price as a real one before speaking it. Erring toward extracting more is the safe direction here: an over-extracted figure blocks a sentence, an under-extracted figure speaks an unverified one.

**Surfaces covered.** All of the following extract to one value, and each was a reachable bypass until it did (issue #8):

| Surface | Value | Was |
|---|---|---|
| `985,000` `985000` `985k` `0.985 million` `٩٨٥٬٠٠٠` | 985,000 | already covered |
| `.8 million` | 800,000 | no match at all |
| `8e5` | 800,000 | exempt counts 8 and 5 |
| `8-million` | 8,000,000 | exempt count 8 |
| `380 000` with U+202F, U+00A0 or U+2009 | 380,000 | allowed 380 plus exempt 000 |
| `-985,000`, `−20%` | negative | the positive counterpart, which is allowed |
| `2026,` (a year before a comma) | 2026, still a year | an unallowed amount, so a correct sentence was BLOCKED |

The ordinary ASCII space is deliberately not a group separator: making it one would fuse "3 bedrooms and 2 towers" into one figure. A group separator only ever joins digits, which is also why a trailing comma no longer reaches the classifier - the over-block in the last row is fixed upstream of classification rather than by stripping punctuation afterwards.

**Normalisation.** Reduce to canonical value before comparison. Multiplier words, percent words and currency tokens live in `data/numerals.yaml`, not in code, because the magnitude and the kind usually sit in the token beside the digits and that token is a word. (Beware the class of error this catches even in humans: AED 2,400,000 is 24 lakh, not 2.4 crore.)

**Policy.**

| Kind | Rule |
|---|---|
| Amounts and bare numbers > 12 | Must be in the allowed set |
| Amounts with a currency token beside them | Must be in the **currency** subset of the allowed set |
| Percentages | Must be in the allowed set |
| Years (1900-2099, standalone) | Must be in the allowed set - a wrong handover year is the evidence exhibit |
| Integers 0-12, non-percent, **no currency token** | Exempt as conversational counts ("three bedrooms", "one question"). A deliberate, documented hole |
| Figures joined by `×`, `*`, `^`, `·` or a superscript digit | Blocked on sight, never computed |

**A currency token voids the exemptions.** The old policy line said a small integer "cannot state a price". The sentence "It starts at AED 12" disproves it, and so does "It starts at AED 2026", where a figure that is an allowed handover year was checked against the year set and spoken as a price. A currency token adjacent to a figure - either side, with or without a space, symbol or Latin word - now makes it an amount, so it is checked rather than exempted. Over-blocking is the free direction: a blocked sentence is recoverable, a spoken price is not.

**Composed arithmetic is refused, not evaluated.** `8 × 10^5` is three integers that are each individually exempt and together state 800,000. The system does no arithmetic (invariant 2) and will not do the model's either, so an operator adjacent to a figure is a violation on its face. The prompt already forbids that syntax, so this costs nothing real. Latin `x` is excluded on purpose: it is the ordinary dimension separator ("2 x 3 metres"), and the caret still catches `8 x 10^5`.

**Allowed set: global (ADR-008), and typed by kind.** Union of every figure in inventory (source + computed) plus `data/whitelist.yaml`. A PRICE is checked against the money subset alone (`AllowedFigures.currency_amounts`): the untyped set also holds square footages and Binghatti's hotline number, so `It starts at AED 380` validated against a size and `It starts at AED 80015` against a phone number. A figure with no currency token beside it still validates against the untyped set, because stating a size or reading the hotline out are things the agent legitimately does. Per-referenced-project scoping is the documented next tier.

**What is still open, in Arabic and Hindi script.** The numeric guarantee rests on digits, and digits normalise in all three scripts. The magnitude and the kind often do not: `٨ مليون درهم` says eight million dirhams, and with `ar` word lists empty in `data/numerals.yaml` the extractor reads a bare 8, classifies it as an exempt count, and the sentence is spoken. The percent SIGN (`٪`, `％`, `﹪`) is language-neutral punctuation and is covered now, as are the Latin-script tokens a model writes whatever language it is speaking (`AED`, `dirhams`, `lakh`, `crore`) - which is the Dubai code-switched register and the common case. What is not covered is a magnitude or currency word written wholly in Arabic or Devanagari script.

This is the same shape of gap as the English-only prohibited patterns below, disclose it the same way, and it closes the same way: a native speaker fills the `VERIFY:` lists in `data/numerals.yaml`. Nobody on the build team may fill them (AGENTS.md), and the mechanism is already proved by tests that inject the words as fixture data, so the fix is a data edit and not a code change. `figures.languages_covered()` reports which languages actually have words, so the true coverage is queryable rather than assumed.

**False positives are the operational risk.** A validator that blocks correct replies gets disabled by the first engineer who hits it on a Friday. Log every violation with the extracted figure and the allowed set; review during the build week; tune the normaliser rather than loosening the check.

**The digit-emission dependency.** The system prompt instructs the model to write figures as plain digits. If it writes "nine hundred and seventy-five thousand", the validator finds nothing to inspect and passes an unverified sentence. This is covered three ways: the prompt instruction, an eval category that pressures it ("say the price in words"), and the fact that verbalisation happens downstream anyway so the model gains nothing by spelling numbers out.

## Validator 2 - prohibited language

`guardrails/prohibited.py`, patterns in `data/prohibited-patterns.yaml` - one language-neutral file, reviewable by a non-engineer.

**English patterns only in the POC. Disclose this in the meeting.** The distinction to draw: the critical guardrail (numeric claims) is language-agnostic because it operates on digits, so the guarantee that a fabricated price cannot be spoken holds in all three languages. The stylistic layer is English-only until a native reviewer writes the Arabic and Hindi patterns. Never ship patterns nobody on the team can read - `VERIFY:` native review.

Both halves of that are now verified by execution rather than asserted, and each has a wrinkle worth carrying into the room:

- **The numeric claim holds across scripts for digits, and only for digits.** `figures.py` normalises Arabic-Indic, extended Arabic-Indic and Devanagari digits along with the Arabic separators `٬` and `٫`, one character to one so verbalisation spans stay valid, and sentence splitting already breaks on `؟` and `।`. A fabricated price written `١٬٢٥٠٬٠٠٠` is caught and an allowed `٩٨٥٬٠٠٠` passes. This is the half a tech lead will expect to be broken. The wrinkle is the one disclosed above: where the magnitude lives in a native WORD rather than the digits (`٨ مليون`), it is not read yet, and the figure keeps its small-integer exemption.
- **The stylistic layer does cover code-switching, which is the register that matters.** Every pattern runs against every sentence whatever language the call is in, so a reply in Arabic that slips into English to say "guaranteed returns" is caught. `language` on a pattern is provenance - the competence its author needed - and is deliberately never used for routing, because routing by the call's language would give exactly this up. What is not covered is a violation written wholly in Arabic or Devanagari script.

The `prohibited_coverage` event at session start reports which languages actually have patterns, so the demo record states the real coverage instead of implying uniform protection.

| Category | Catches | Action |
|---|---|---|
| Return guarantees | guaranteed/assured/promised return, yield, appreciation; risk-free; can't lose | Block |
| Advice framing | "you should invest", "I recommend you buy" | Block |
| Future certainty | "will appreciate", "prices will rise", "certain to" | Block |
| Regulatory overreach | asserting visa, mortgage, or tax outcomes as certain | Block |
| Competitor disparagement | named competitor plus negative claim | Block |

## Validator 3 - identifier integrity

Every `shortlist_ids` entry must resolve to a real inventory record. An unresolvable id is a guardrail failure, not a rendering fallback; silent dropping is forbidden because it hides exactly the failure mode we claim to prevent.

## Validator 4 - PII redaction

Applied to the emitted event stream today, not deferred: the JSON lines the agent emits (stdout, optional file sink) carry **no free text at all** - enumerated and numeric telemetry only (event names, outcomes, timings, counts, token usage, tool names, ids). Every free-text field is redacted by default because free text can carry buyer-derived content by more routes than the obvious one: the agent's own sentences read the buyer's budget back for confirmation, guardrail violation details quote the offending figures, escalation reasons paraphrase complaints, booking slots are "in the buyer's own words". One less obvious route is named explicitly because it has now leaked twice in review: an exception message from a provider call quotes a slice of the response body, and that request's payload was the buyer's transcript - so error/detail fields on `brief_error`, `brief_fallback`, `llm_failure` and `session_error` are redacted too. The classification lives in code (`adapter/events.py`): every emitted event type must appear in exactly one of `_REDACTED_FIELDS` or `CLEAR_EVENTS` (the latter with a stated reason), enforced by a test that discovers event names from the adapter source itself - adding an event without classifying it fails the suite. Anything that needs the text - the ambassador view, the audit - reads the full-fidelity in-process records, never the emitted stream. `AMBASSADOR_EVENT_VERBOSE=true` restores full emission for local development and is never set for demos or deployments (`docs/07-`). Hashing of contact details before any durable event store remains `PHASE-2:`.

### The one surface that is not redacted

Two surfaces leave this process and they carry deliberately different things. Naming both here, because a reader who finds only the redacted one will reasonably assume there is nothing else, and that assumption is how the second one stops being reviewed.

| Surface | Carries | Reaches |
|---|---|---|
| stdout and the optional file sink | enumerated and numeric telemetry only, per the rule above | anything that scrapes the process, anything durable |
| the events bridge (`adapter/events_bridge.py`) | the **unredacted** records: buyer utterance, model sentence, validator detail, brief | one local process holding a per-session token |

The bridge exists because the demo surface needs exactly what validator 4 withholds: a transcript rail shows what the buyer said, the ambassador view shows the brief, and a guardrail decision is illegible without the sentence it objected to (`web/README.md` for the surface itself, issue #9). Building it out of the redacted stream is not possible, and the alternative - setting `AMBASSADOR_EVENT_VERBOSE=true` for the demo - is worse, because that routes buyer text into stdout and the file sink, which is the thing this validator forbids.

It is bounded twice, and neither bound is sufficient alone:

- **Bound to `127.0.0.1`.** The module refuses any other host rather than treating it as configuration. Full-fidelity buyer data never leaves the machine, which is the ADR-012/013 posture.
- **A per-session random token, required as the first line of every connection.** This exists because loopback is not a boundary against a page running in the presenter's own browser: any web page can reach 127.0.0.1 and port-scan whatever answers. Loopback stops the network; the token stops the browser.

The token is held by the Next server and never by the browser, which only ever talks same-origin to Next. It is delivered through a `0600` handshake file and appears on no stream - the `events_bridge` event carries the host and port and deliberately not the token, because putting the credential for the unredacted surface into the sink that exists because it is redacted would defeat both.

The bridge is **read-only**: after the token line it never reads from the socket again. There is no command channel, and the `GUARDRAIL_MODE`/`PROMPT_MODE` toggles are deliberately not folded into it - both are read at session start, so a control channel is a different question with a different threat model, and adding one would turn a surface that can watch the agent into one that can change it.

It is **off unless `AMBASSADOR_BRIDGE_HANDSHAKE` names a path**, and that same variable is what tells the consumer where to connect, so an enabled bridge always has a reader that was told about it. A listening socket carrying buyer transcripts is not something to have on by default.

## Failure handling

Every path ends in composed, localised speech and a route to a human. The buyer never hears silence, an error tone, or an error message. Every failure emits an event.

```
guardrail violation, nothing spoken yet -> cancel, regenerate once (violation named), then fallback
guardrail violation, audio already played -> composed bridge + correct escalation (no regeneration)
brief extraction invalid -> one repair retry -> keep last good brief, log
LLM timeout/error -> spoken fallback + escalate
STT low confidence / policy trigger -> confirmation turn (docs/04-)
STT failure x3 -> warm escalation
TTS failure -> retry once, fallback voice, then transcript on screen + callback offer
network drop -> reconnect once, then callback offer
buyer silence -> one gentle prompt, then close politely
```

## Demo modes: the defence-in-depth demonstration

`GUARDRAIL_MODE=warn` alone will underwhelm: with the validator off, the ambassador prompt still instructs strict grounding, and a well-aligned model will usually refuse the trap question anyway. The demo would show the toggle doing nothing.

So the demonstration pairs two flags and frames it honestly:

- `PROMPT_MODE=naive` + `GUARDRAIL_MODE=warn`: a deliberately generic assistant prompt, labelled "typical chatbot configuration" in the UI. This simulates the real failure modes - prompt drift, injection, sycophancy.
- The trap is a leading question, not a cold one: "I read that Binghatti Marina Heights starts at 800,000 - is that right?" Planted false premises elicit confirmation far more reliably than cold invention, and they are the realistic buyer behaviour anyway.
- Then `PROMPT_MODE=ambassador` + `GUARDRAIL_MODE=enforce`, same question, and the refusal + escalation.

The narrative: "prompts drift, get overridden, and get injected; here is what holds when the prompt layer fails." That is a stronger and more honest claim than pretending the prompt does nothing. Rehearse until the naive configuration misbehaves reliably; keep a recorded run as backup (`docs/07-`).

## UAE regulatory map

Not legal advice; the map of what to raise with Binghatti's legal function. `VERIFY:` all of it with a UAE-qualified adviser before production.

- **Real estate marketing.** Dubai property advertising sits under DLD/RERA; marketing material generally requires a Trakheesi permit with the number displayed. An AI surface quoting prices is plausibly advertising. Question for their legal team: does conversational output fall inside existing permit coverage, and must a permit reference appear in the interface? Misleading-claims prohibition is what Validator 2 exists for. Off-plan contractual matters (escrow, Oqood, SPA terms) are never characterised by the agent - always a human.
- **Data protection (PDPL, Federal Decree-Law No. 45 of 2021).** Lawful basis and consent before contact details; purpose limitation on the brief; retention period and deletion route for transcripts (`PHASE-2:` implement; `VERIFY:` the period). **Cross-border inference is assumption A5 and the most likely production blocker - raise it in the first meeting unprompted; it is a credibility move.**
- **Voice specifics.** Disclosure and transcription notice at call start, in the selected language, from fixed native-reviewed copy in `data/disclosures.yaml` - never model-generated, and not interruptible (the first few seconds of the call ignore barge-in so the disclosure always completes). The POC stores no raw audio, which defers the voice-as-biometric-data question entirely; `VERIFY:` its PDPL status with a UAE adviser before any production build records calls.
- **AI disclosure.** A caller hearing a warm natural voice assumes a person unless told otherwise. Spoken disclosure is not optional in a regulated sales channel.

## The audit trail

Every buyer-facing statement is reconstructable: session, timestamp, inventory version, model, prompt/guardrail mode, generated sentences, and what was actually spoken at chunk granularity (a barge-in marks the chunk incomplete - claiming the buyer heard a full sentence they did not defeats the record's purpose). A buyer asserting "your agent told me handover was 2025" becomes a lookup, not a dispute. Frame this in the meeting as risk their current phone channel carries and this one retires.

## Standard disclaimer block

Rendered verbatim where figures are shown; not editable by the model. `VERIFY:` wording with Binghatti legal.

> Figures shown are indicative and drawn from published project information. They are not an offer, a valuation, or a forecast. Property values can fall as well as rise. Visa and mortgage eligibility are determined by the relevant UAE authorities and lenders. Obtain independent financial and legal advice before committing.
