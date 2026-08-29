"""Which speech recogniser the session uses, and why the choice matters.

Two shapes sit behind one selector, and they are not interchangeable on
latency:

`openrouter` is WHOLE-UTTERANCE. Nothing starts until the buyer stops
speaking, so the whole transcription cost lands after endpoint, on the
critical path. Measured on the hosted path: p50 1081ms, p90 2826ms, worst
43174ms, against a 100-300ms budget line that was written for a streaming
recogniser and never reconciled with this choice (docs/04-).

`deepgram` is STREAMING. Partials arrive while the buyer is still talking, so
only the tail after endpoint is charged - which is the assumption the latency
budget was built on. It also closes two defects the whole-utterance path could
not: keyterm boosting for names the model mangles ("Binghatti" came back as
"Bint Jbeil"), and numerals, so figures arrive as digits rather than words and
a deterministic parse of buyer speech has something to read.

Selection is config, per ADR-006: no adapter of ours wraps either one.
"""

from __future__ import annotations

import logging

from livekit.agents import stt

from .config import Settings
from .stt_openrouter import OpenRouterSTT

logger = logging.getLogger("ambassador.stt")

# Names the recogniser should not have to guess at. Deepgram takes these as
# keyterms; the whole-utterance path has no equivalent, which is why it kept
# mishearing the client's own name. Project and area names come from inventory
# at call time; these are the fixed brand terms.
BRAND_KEYTERMS: tuple[str, ...] = (
    "Binghatti",
    "Bugatti Residences",
    "Burj Binghatti",
    "Jacob and Co",
    "Skyrise",
    "Jumeirah Village Circle",
    "Business Bay",
    "Al Jaddaf",
    "Meydan",
    "dirhams",
    "AED",
)


def build_stt(settings: Settings, *, keyterms: tuple[str, ...] = BRAND_KEYTERMS):
    """The recogniser for this session, or None when STT is switched off.

    Raises ValueError on an unknown provider rather than silently falling back:
    a session that quietly runs the slow path is worse than one that refuses to
    start, because the latency meter is the only place it would show.
    """
    if not settings.stt_enabled:
        return None

    provider = settings.stt_provider.lower()

    if provider == "deepgram":
        from livekit.plugins import deepgram

        return deepgram.STT(
            model=settings.deepgram_model,
            language=settings.deepgram_language(settings.language),
            api_key=settings.deepgram_api_key,
            interim_results=True,
            # Figures must arrive as digits. The whole-utterance path returned
            # "two million", which no deterministic parse of buyer speech can
            # read, and ADR-011's confirmation policy depends on reading one.
            numerals=True,
            keyterm=list(keyterms),
            punctuate=True,
        )

    if provider == "openrouter":
        return OpenRouterSTT(
            api_key=settings.openrouter_api_key,
            model=settings.stt_model(settings.language),
            language=settings.language,
        )

    raise ValueError(
        f"unknown STT_PROVIDER {settings.stt_provider!r}; expected 'deepgram' or 'openrouter'"
    )


def describe(node: stt.STT | None) -> dict[str, object]:
    """What to put in the stt_enabled event, without leaking a key."""
    if node is None:
        return {"provider": None}
    provider = type(node).__module__.split(".")[-2]
    streaming = provider == "deepgram"
    return {
        "provider": provider,
        "model": getattr(node, "model", None) or getattr(node, "_opts", None)
        and getattr(node._opts, "model", None),
        "streaming": streaming,
        # The number that decides whether the budget line applies as written.
        "charges_after_endpoint": "tail only" if streaming else "whole utterance",
    }
