"""Sentence boundaries for a streaming model reply.

Core rather than adapter because two callers need the SAME boundaries and one
of them may not import the voice stack: `adapter/interception.py` splits a live
LLM stream on its way to TTS, and `evals/` replays recorded and live replies
through the identical pipeline headless (ADR-002). A second copy of this regex
is a drift hazard with a specific shape - the Arabic `؟` and the Devanagari `।`
are easy to leave out of a reimplementation, and the miss would show up as an
eval passing in English and the live call mis-splitting in Arabic.

Pure: no I/O, no framework import, no environment.
"""

import re

# A boundary is terminal punctuation followed by whitespace. Requiring the
# whitespace is what keeps "AED 1.5 million" and "Q4 2026" intact while the
# stream is still arriving; the trailing fragment is flushed when the stream
# ends.
_BOUNDARY = re.compile(r"(?<=[.!?؟।])\s+")


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split a streaming buffer into completed sentences plus the remainder."""
    if not buffer:
        return [], ""
    parts = _BOUNDARY.split(buffer)
    remainder = parts.pop() if parts else ""
    complete = [p.strip() for p in parts if p.strip()]
    return complete, remainder
