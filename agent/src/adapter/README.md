# LiveKit adapter (written day 1)

The thin layer that wraps the headless core into LiveKit Agents (ADR-002/ADR-005): the session definition, the sentence hook that calls `ambassador.guardrails.process_sentence()` between LLM and TTS, the function tools (`escalate_to_human`, `offer_booking`, `confirm_booking`), and the async post-turn lead-brief extraction task.

Framework imports are allowed here and nowhere else - `test_core_has_no_framework_imports` enforces the boundary. Day 1 gate: all three hooks proven on the real framework (text interception, mid-turn function tool, post-turn task) or switch to Pipecat immediately (docs/06-).

## `events_bridge.py`

The one surface that carries the **unredacted** event stream, for the demo UI
(issue #9). Everything else that leaves the process is redacted by validator 4,
and that is not changing - the bridge is a separate, deliberately narrow surface
for the one consumer that needs the buyer's actual words.

Bounded twice, and neither bound is sufficient alone: bound to `127.0.0.1`
(refused otherwise, not treated as configuration) **and** a per-session random
token required as the first line of every connection. Loopback is not a boundary
against a page running in the presenter's own browser; the token is. Read-only,
off unless `AMBASSADOR_BRIDGE_HANDSHAKE` names a path. Rationale in the module
docstring and in `docs/03-`, "the one surface that is not redacted".
