"""Mask vendor SDK log lines, which print whatever the upstream handed them.

Our `EventLog` classifies every field it emits and redacts the free text. That
discipline stops at our own stream: the vendor SDKs log whole upstream error
BODIES at WARNING, and one of those bodies carried an OpenRouter account user id
into the worker log. A body that echoes a prompt would put a buyer's words there
too, beside events that carefully do not carry them.

WHY THIS IS A HANDLER FILTER AND NOT A LOGGER FILTER. `logging` does not apply a
logger's filters to records from its children - `logging.getLogger("livekit")`
never sees `livekit.agents.voice.agent_activity` - but the parent's HANDLER
emits them regardless. A mask on the parent logger would therefore pass its own
tests and leak exactly where vendor code lives. It goes on the handlers, and the
record's own name decides whether it is masked.

MASKED, NEVER SILENCED. Lowering the vendor level would have been less code and
the wrong trade: the 429 that killed brief extraction was only visible because
the SDK complained. Name, level and diagnosis all survive; only the values go.

The vocabulary is deliberately about SHAPES, not names. `config._is_credential`
answers "is this FIELD a credential" for our own dataclass, where the name is
known and trustworthy. Here there are no field names - only a blob of someone
else's prose - so a value is masked because it looks like a secret, an address,
a DSN, an account id or a sentence in a transcript.
"""

from __future__ import annotations

import logging
import re
from typing import Final

MASK: Final = "[masked]"

# Records whose logger name starts with one of these are vendor records. Ours
# all live under `ambassador.`, so this cannot touch them.
VENDOR_PREFIXES: Final = (
    "livekit",
    "openai",
    "httpx",
    "httpcore",
    "aiohttp",
    "asyncpg",
)

# Longest-first, because an email inside a DSN and a token inside a JSON body
# both need the outer shape to win.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # A DSN's userinfo, masked whole: the user half names the Supabase project
    # and the password half is a password.
    ("dsn", re.compile(r"(?<=://)[^\s/@]+:[^\s/@]+(?=@)")),
    # `Bearer <token>` and the bare `sk-`/`sk-or-` shapes the providers issue.
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}")),
    # name=value where the NAME says credential: the house vocabulary from
    # `config._is_credential`, applied to someone else's prose.
    (
        "named_secret",
        re.compile(
            r"(?i)\b\w*(?:key|keys|secret|secrets|token|tokens|password|passwords"
            r"|code|codes)\w*\b(\s*[:=]\s*|['\"]\s*:\s*)['\"]?[^\s,'\"}\)]{6,}"
        ),
    ),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Provider account ids. `user_...` is the shape ryan found in the log.
    ("account_id", re.compile(r"\b(?:user|org|acct|account)_[A-Za-z0-9]{6,}\b")),
)

# A quoted prompt or transcript echoed back inside an error body. Keyed on the
# FIELD NAME the vendors use rather than on the sentence, because a buyer's
# words have no shape to match - which is also why this is the one rule that
# can only ever be best-effort, and why the event stream stays the real
# guarantee.
# `message` is deliberately NOT in this list, and a test caught me including
# it: vendor error bodies put their own DIAGNOSIS there
# (`'message': 'insufficient_quota'`), so masking it turns the warning we kept
# these lines for into a row of markers. The names here are the ones that carry
# a PAYLOAD echo rather than an explanation.
_ECHOED_TEXT: Final = re.compile(
    r"(?i)(['\"]?(?:prompt|input|content|text|transcript|utterance"
    r"|echoed_prompt)['\"]?\s*[:=]\s*)(['\"])(.*?)(\2)"
)


def mask_text(text: str) -> str:
    """The house mask for a line of someone else's prose.

    Idempotent: `MASK` matches none of the patterns, so masking a masked line
    changes nothing further. That matters because a filter can be installed
    more than once in a process's life.
    """
    masked = _ECHOED_TEXT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{MASK}{m.group(4)}", text
    )
    for _name, pattern in _PATTERNS:
        masked = pattern.sub(MASK, masked)
    return masked


def is_vendor(logger_name: str) -> bool:
    return any(
        logger_name == prefix or logger_name.startswith(prefix + ".")
        for prefix in VENDOR_PREFIXES
    )


class VendorMask(logging.Filter):
    """Applies the house mask to vendor records, on their way to a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not is_vendor(record.name):
            return True
        try:
            rendered = record.getMessage()
        except Exception:
            # A record whose own formatting raises is a vendor bug, and one
            # this filter must not turn into a lost warning.
            return True
        masked = mask_text(rendered)
        if masked != rendered:
            # Replace the message AND drop the args: the args are where the
            # unmasked values were, and a formatter would put them back.
            record.msg = masked
            record.args = ()
        return True


def install_vendor_log_mask() -> int:
    """Attach the mask to every root handler. Returns how many it added.

    Idempotent, because `prewarm` runs once per process and `main` once per
    start: a stacked filter would mask an already-masked line and turn a
    diagnosis into a row of markers.

    Handlers added AFTER this runs are not covered, which is why the worker
    calls it after the framework has configured logging rather than before.
    """
    root = logging.getLogger()
    added = 0
    for handler in root.handlers:
        if not any(isinstance(f, VendorMask) for f in handler.filters):
            handler.addFilter(VendorMask())
            added += 1
    return added
