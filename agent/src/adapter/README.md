# LiveKit adapter (written day 1)

The thin layer that wraps the headless core into LiveKit Agents (ADR-002/ADR-005): the session definition, the sentence hook that calls `ambassador.guardrails.process_sentence()` between LLM and TTS, the function tools (`escalate_to_human`, `offer_booking`, `confirm_booking`), and the async post-turn lead-brief extraction task.

Framework imports are allowed here and nowhere else - `test_core_has_no_framework_imports` enforces the boundary. Day 1 gate: all three hooks proven on the real framework (text interception, mid-turn function tool, post-turn task) or switch to Pipecat immediately (docs/06-).
