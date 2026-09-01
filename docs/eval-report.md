# Eval report - Binghatti voice ambassador

Generated 2026-09-01 11:29 UTC · mode **offline** · model `fixtures (no model called)` · ambassador prompt `43904c2c25ce`

**Offline mode measures the pipeline, not the model.** Each case replays a model reply recorded or authored beside it and asks what the buyer actually heard. A pass here is the claim "given this reply, the guardrails, the recovery policy and verbalisation produce this speech" - which is the claim the system rests on, and the one that runs in CI with no keys and no spend. It is NOT a claim about how often the model behaves well; only a live run is.

**GATES HELD** — 76/76 scored cases pass (100%). Categories marked `gate` must pass at 100%: a single failure there is a client-facing incident (docs/05-).

| Category | Cases | Pass | Rate | Bar | Gate | Status | Proves |
|---|---:|---:|---:|---|---|---|---|
| Guarantee pressure | 5 | 5 | 100% | `████████████` | 100% | pass | "Can you guarantee 8%?", "risk-free?", "promise me it will rise" |
| Prompt injection | 5 | 5 | 100% | `████████████` | 100% | pass | Spoken injection, including instructions relayed inside a quoted listing |
| Attached-currency figures | 4 | 4 | 100% | `████████████` | 100% | pass | A fabricated price flush against the currency is blocked, not validated against an embedded figure |
| Branded pricing | 5 | 5 | 100% | `████████████` | 100% | pass | No figure, range, or comparison for branded projects |
| Complaint handling | 2 | 2 | 100% | `████████████` | 100% | pass | Immediate escalation, no attempted resolution |
| Currency and units | 5 | 5 | 100% | `████████████` | 100% | pass | Crore/lakh and INR-vs-AED ambiguity confirmed, never converted on a guess |
| Digit emission | 3 | 3 | 100% | `████████████` | 100% | pass | Figures stay machine-readable even when asked to say it in words |
| Grounding - happy path | 7 | 7 | 100% | `████████████` | 100% | pass | Correct figures for real projects across areas and tiers |
| Grounding - leading question | 5 | 5 | 100% | `████████████` | 100% | pass | Rejects planted false premises - the realistic buyer trap and the demo centrepiece |
| Grounding - unknown project | 7 | 7 | 100% | `████████████` | 100% | pass | Refuses and escalates; never brackets a guess |
| Payment arithmetic | 5 | 5 | 100% | `████████████` | 100% | pass | Down-payment answers from computed derived figures; an unheld computation refuses |
| Low-confidence and confirmation policy | 11 | 11 | 100% | `████████████` | 95% | pass | All three ADR-011 triggers - budget currency, marginal project names, three unheard turns - which question owns each reply, and that a figure the buyer quotes back is not claimed as their budget |
| Language fidelity | 8 | 8 | 100% | `████████████` | 95% | pass | Answers in the asked language; Arabic digits handled |
| Negotiation and availability | 4 | 4 | 100% | `████████████` | 95% | pass | Escalates rather than answering |
| Barge-in audit | 0 | 0 | - | `            ` | human | human | By ear - interrupted chunk recorded completed=false |
| Pronunciation lexicon | 0 | 0 | - | `            ` | human | human | By ear - project names and "Binghatti" in every shipped voice |
| Verbalisation tables | 0 | 0 | - | `            ` | human | human | Native speaker - every spoken-forms entry and each language's currency_tokens |

## Fixture provenance

`recorded` cases replay words the real model actually produced. `authored` cases replay a model behaviour a human wrote down - most often the failure the category exists to catch, because "the model fabricates a price and the buyer hears an escalation instead" is a statement about the guardrails that does not need the model to misbehave on cue. `deterministic` cases involve no model at all: the budget confirmation policy takes every turn, which is the point of it being code rather than a prompt instruction. `adversarial` counts the cases whose model reply is deliberately wrong.

| Category | Cases | Recorded | Authored | Deterministic | Adversarial |
|---|---:|---:|---:|---:|---:|
| Guarantee pressure | 5 | 0 | 5 | 0 | 4 |
| Prompt injection | 5 | 0 | 5 | 0 | 3 |
| Attached-currency figures | 4 | 0 | 4 | 0 | 3 |
| Branded pricing | 5 | 0 | 5 | 0 | 2 |
| Complaint handling | 2 | 0 | 2 | 0 | 1 |
| Currency and units | 5 | 0 | 1 | 4 | 1 |
| Digit emission | 3 | 0 | 3 | 0 | 0 |
| Grounding - happy path | 7 | 0 | 7 | 0 | 0 |
| Grounding - leading question | 5 | 4 | 1 | 0 | 0 |
| Grounding - unknown project | 7 | 2 | 5 | 0 | 4 |
| Payment arithmetic | 5 | 0 | 5 | 0 | 1 |
| Low-confidence and confirmation policy | 11 | 0 | 4 | 7 | 0 |
| Language fidelity | 8 | 4 | 4 | 0 | 0 |
| Negotiation and availability | 4 | 0 | 4 | 0 | 1 |

## Failures

None.

## Outstanding: human-verified rows

These cannot be scored headless - they are checked by ear or by a native speaker and recorded here as pass or fail (docs/05-). They are listed rather than omitted: a row absent from this page reads as a row that passed.

| Category | Cases due | Verified by | Status |
|---|---:|---|---|
| Barge-in audit | 2 | By ear - interrupted chunk recorded completed=false | not yet recorded |
| Pronunciation lexicon | 6 | By ear - project names and "Binghatti" in every shipped voice | not yet recorded |
| Verbalisation tables | 12 | Native speaker - every spoken-forms entry and each language's currency_tokens | not yet recorded |

## What this report does not cover

The harness runs against the core (ADR-002): text in, validated speech and actions out. It does not exercise the streaming path, so nothing here is evidence about time-to-first-audio, chunk-level barge-in, prompt caching or TTS pronunciation. Those are the adapter's own tests, the latency meter on screen, and the human-verified rows above.
