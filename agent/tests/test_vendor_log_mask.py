"""Vendor SDK log lines must not carry what our own stream redacts.

Our `EventLog` redacts correctly. The leak is one layer over: vendor SDKs print
whole upstream error BODIES at WARNING, and ryan found an OpenRouter account
user id in the 19:23Z worker log that way. If an upstream body ever echoes a
transcript fragment, it lands unmasked beside events that carefully do not.

Every case asserts on HANDLER OUTPUT rather than on the filter, because that is
where a leak actually happens - and because a filter attached to the wrong
object passes its own unit test while masking nothing. `logging` makes that easy
to get wrong: a filter on a logger is NOT applied to records from its children,
though its handler still emits them.

Imports inside each test so RED reads N failed = N cases.
"""

from __future__ import annotations

import io
import logging

import pytest

# Fakes, chosen to look exactly like the real things they stand for.
# Assembled from parts and made unmistakably synthetic ON PURPOSE. The first
# version of this fixture was a realistic `sk-or-v1-<64 hex>` string, and
# GitHub's push protection rejected the branch for it - correctly: a repo
# cannot tell my fake from a live key, and neither can a person skimming a
# diff. It still has to MATCH the mask's `sk-` shape, which low-entropy
# text does.
TOKEN = "sk-" + "or-v1-" + "NOTAREALKEY" * 3
EMAIL = "buyer.name@example.com"
DSN = "postgresql://postgres.NOTAREALREF:NOTAREALPASSWORD@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
USER_ID = "user_2f8Kq9LmNpQrStUvWxYz"
TRANSCRIPT = "my budget is about two million dirhams"

VENDOR_LOGGERS = ("livekit.agents", "livekit.plugins.openai", "openai")


@pytest.fixture
def captured() -> tuple[io.StringIO, logging.Handler]:
    """A handler on the root, which is where a worker's logging ends up."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    try:
        yield stream, handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def _upstream_body() -> str:
    """One warn line shaped like the ones that actually leak: an SDK repeating
    the whole upstream error body it was handed."""
    return (
        "OpenAI API error: {'error': {'message': 'insufficient_quota', "
        f"'metadata': {{'user': '{USER_ID}', 'email': '{EMAIL}', "
        f"'authorization': 'Bearer {TOKEN}', 'dsn': '{DSN}', "
        f"'echoed_prompt': 'turn 3 buyer: {TRANSCRIPT}'}}}}}}"
    )


@pytest.mark.parametrize("logger_name", VENDOR_LOGGERS)
def test_no_marker_reaches_the_handler_from_a_vendor_logger(
    logger_name: str, captured
) -> None:
    from adapter.logmask import install_vendor_log_mask

    stream, _handler = captured
    install_vendor_log_mask()

    logging.getLogger(logger_name).warning(_upstream_body())
    written = stream.getvalue()

    for marker, label in (
        (TOKEN, "bearer token"),
        (EMAIL, "email"),
        ("NOTAREALPASSWORD", "DSN password"),
        ("postgres.NOTAREALREF", "DSN user"),
        (USER_ID, "account user id"),
        (TRANSCRIPT, "transcript fragment"),
    ):
        assert marker not in written, f"{label} reached the handler via {logger_name}"


def test_a_child_of_a_vendor_logger_is_masked_too(captured) -> None:
    """The trap this whole module is built around. A filter attached to
    `livekit` would NOT see records from `livekit.agents.something` - `logging`
    does not apply a logger's filters to its children - while the handler emits
    them anyway. That mask would pass its own unit test and leak in production.
    """
    from adapter.logmask import install_vendor_log_mask

    stream, _handler = captured
    install_vendor_log_mask()

    logging.getLogger("livekit.agents.voice.agent_activity").warning(_upstream_body())

    assert TOKEN not in stream.getvalue()
    assert TRANSCRIPT not in stream.getvalue()


def test_the_warning_survives_masking(captured) -> None:
    """Masked, not silenced. We want these warnings: the 429 that killed brief
    extraction was only visible because the SDK complained."""
    from adapter.logmask import install_vendor_log_mask

    stream, _handler = captured
    install_vendor_log_mask()

    logging.getLogger("livekit.agents").warning(_upstream_body())
    written = stream.getvalue()

    assert "livekit.agents WARNING" in written, "name and level must survive"
    assert "insufficient_quota" in written, "the diagnosis must survive"
    assert "OpenAI API error" in written


def test_our_own_logger_is_untouched(captured) -> None:
    """The mask is for vendor lines. Our own loggers already say only what they
    mean to, and masking them would make an operator distrust both."""
    from adapter.logmask import install_vendor_log_mask

    stream, _handler = captured
    install_vendor_log_mask()

    logging.getLogger("ambassador.agent").warning(
        "opening in 'en': no native-authored disclosure for 'ar'"
    )

    assert "no native-authored disclosure for 'ar'" in stream.getvalue()


def test_installing_twice_does_not_double_the_filter() -> None:
    """`prewarm` runs per process and `main` runs once; an install that stacked
    would mask a masked string and turn a diagnosis into a row of markers."""
    from adapter.logmask import VendorMask, install_vendor_log_mask

    install_vendor_log_mask()
    install_vendor_log_mask()

    root = logging.getLogger()
    for handler in root.handlers:
        masks = [f for f in handler.filters if isinstance(f, VendorMask)]
        assert len(masks) <= 1, masks


def test_the_mask_is_a_pure_function_of_the_text() -> None:
    """So it can be reasoned about without a logger, and so the vocabulary is
    one place rather than one per call site."""
    from adapter.logmask import mask_text

    masked = mask_text(_upstream_body())

    assert TOKEN not in masked
    assert EMAIL not in masked
    assert USER_ID not in masked
    assert "NOTAREALPASSWORD" not in masked
    assert "insufficient_quota" in masked
    # Idempotent: masking a masked line changes nothing further.
    assert mask_text(masked) == masked
