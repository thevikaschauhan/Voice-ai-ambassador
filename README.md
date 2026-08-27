# Binghatti voice ambassador - POC

A multilingual voice agent for Binghatti Developers (Dubai), built by Future Focus Infotech for a technical evaluation meeting. It answers a prospective buyer in their language, qualifies them in conversation, recommends real inventory, and books a human ambassador - and it cannot speak a figure that is not in Binghatti's records, because every sentence is validated in code before it is synthesised into audio.

## Read in this order

Every file listed here exists. If one goes missing, that is a defect.

| File | What it settles |
|---|---|
| `AGENTS.md` | Canonical operating rules for all coding agents. Read first, every session. |
| `CLAUDE.md` | Claude Code specifics. Points at `AGENTS.md`. |
| `docs/00-brief.md` | Client context, why voice, scope, assumptions register |
| `docs/01-architecture.md` | Technical design: headless core, two-channel turn, module layout, ADRs |
| `docs/02-data-contracts.md` | Every schema: inventory, lead brief, guardrail results, events |
| `docs/03-guardrails.md` | Validators, failure handling, UAE compliance map, demo modes |
| `docs/04-voice.md` | Latency budget, dialects, closed-set verbalisation, lexicon, barge-in |
| `docs/05-evals.md` | Eval harness, case categories, pass thresholds |
| `docs/06-build-plan.md` | The five-day plan, gates, risk register. Authoritative on scope. |
| `docs/07-demo-runbook.md` | Meeting script, open-mic strategy, fallbacks, asks |
| `docs/08-cost-model.md` | Per-call unit economics |

## Layout

```
agent/   Python: the ambassador core (guardrails, inventory, verbalisation) + LiveKit adapter
web/     Next.js demo surface (built day 4; see web/README.md)
data/    Language-neutral data files: inventory, prohibited patterns, spoken forms, whitelist
docs/    The knowledge base above
```

## The five invariants

Everything in this repository exists to protect these. They are enforced in code, not prose.

1. **The model never sources facts.** Every project name, price, size, date and payment figure comes from `data/inventory.json`.
2. **The model never does arithmetic.** Derived figures (down payments, instalments) are computed deterministically at inventory load time and handed to the model pre-computed.
3. **Every model output is validated** before it reaches a user, a log, or a speaker.
4. **Guardrails are code, and the safe path is the only path.** No audio is synthesised from text that has not passed validation. The pipeline types make the wrong ordering unrepresentable, and a test asserts it anyway.
5. **Escalation to a human is a designed feature**, not an error state.

## Status markers

- `VERIFY:` - a figure or rule from public sources that must be confirmed with Binghatti or a UAE-licensed adviser. Never silently resolve one.
- `STUB:` - deliberately faked for the POC, with the real integration named.
- `PHASE-2:` - out of scope for the POC, documented so the client sees the roadmap.

## Quick start

```
cd agent
uv sync
uv run pytest        # the pure core is fully testable without any voice stack
```
