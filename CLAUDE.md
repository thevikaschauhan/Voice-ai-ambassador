# Claude Code notes

**`AGENTS.md` is canonical. Read it first, every session.** This file only adds Claude Code specifics; it never duplicates rules, so it cannot drift from them.

## Commands

```
cd agent && uv sync            # install
cd agent && uv run pytest      # the core test suite; must pass before any summary claims "done"
```

## Session habits

- Scope questions go to `docs/06-build-plan.md` before building anything. If a task is not in its "ships" table, it is faked, deferred, or a mistake.
- When you learn something project-specific or get corrected, offer to record it in the "Project learnings" section of `AGENTS.md`.
- Voice-framework work (LiveKit adapter, day 1+) touches real vendor APIs that move fast: check current LiveKit Agents docs rather than trusting training data, and keep all framework imports out of `agent/src/ambassador/` core modules (ADR-002).
- Figures in `data/inventory.json` are placeholders until Binghatti supplies a price sheet. Treat every one as `VERIFY:`. Never present them as fact in copy, tests may use them freely.
