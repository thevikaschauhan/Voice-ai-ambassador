"""Mask vendor SDK log lines, which print whatever the upstream handed them.

Our `EventLog` classifies every field it emits and redacts the free text. That
discipline stops at our own stream: the vendor SDKs log whole upstream error
BODIES at WARNING, and one of those bodies carried an OpenRouter account user id
into the worker log. A body that echoes a prompt would put a buyer's words there
too, beside events that carefully do not carry them.

WHY THIS IS A RECORD FACTORY. The mask has to be in place before the framework
attaches its handler, because in the main process it IS: `import adapter.agent`
finds `logging.getLogger().handlers == []`, and `cli.run_app` only then calls
`cli.log.setup_logging`, which does `root.addHandler(...)`. The first version of
this module filtered the handlers, so the main-process install added zero
filters and every vendor record the main process logged - registration, drains,
the pooler warnings - went out unmasked. The job path worked only because
`proc_main` attaches its handler before `prewarm` runs, which is luck, not
design.

`logging.setLogRecordFactory` has none of that timing: it masks at record
CREATION, so every handler that exists now or arrives later emits an
already-masked record. It also sidesteps the trap that a filter on a logger is
NOT applied to records from its children - `logging.getLogger("livekit")` never
sees `livekit.agents.voice.agent_activity`, though its handler emits them
regardless - because the record's own name is what decides.

We WRAP the previous factory rather than replace it, and mark ours so a second
install is a no-op: `prewarm` runs once per job process and `main` once per
start.

Two limits, stated rather than papered over. A record created BEFORE the install
is not covered, in either process (`prewarm` is the earliest hook the framework
offers inside a job process). And a record forwarded from a job process is
re-created in the main process by `ipc/log_queue.py` from a pickle, which
bypasses any factory - it is masked at its origin instead, which is why the
install belongs in both processes.

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


def mask_record(record: logging.LogRecord) -> None:
    """Apply the house mask to a vendor record, in place.

    Renders the message first, because the values live in `args` as often as in
    `msg`, and then drops the args: a formatter handed the original args would
    put the unmasked values straight back.
    """
    if not is_vendor(record.name):
        return
    try:
        rendered = record.getMessage()
    except Exception:
        # A record whose own formatting raises is a vendor bug, and one this
        # mask must not turn into a lost warning.
        return
    masked = mask_text(rendered)
    if masked != rendered:
        record.msg = masked
        record.args = ()


# Set on our factory so a second install can recognise its own work. An
# attribute rather than a module-level flag, because the thing to ask about is
# the factory that is actually installed, not what this module remembers doing.
_INSTALLED: Final = "_ambassador_vendor_mask"


def install_vendor_log_mask() -> None:
    """Mask vendor records at creation, for the life of the process.

    Idempotent, and safe at any point relative to the framework's own logging
    setup: that independence is the whole reason it is a factory. See the module
    docstring for the order that made the previous handler-filter version dead
    code in the main process.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, _INSTALLED, False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        mask_record(record)
        return record

    setattr(factory, _INSTALLED, True)
    logging.setLogRecordFactory(factory)
