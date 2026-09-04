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


# ---------------------------------------------------------------------------
# THE PRODUCTION ORDER.
#
# god measured what I had claimed instead of measuring: `import adapter.agent`
# leaves `logging.getLogger().handlers == []`, so the `__main__` install added
# ZERO filters, and `cli.run_app` only then calls
# `livekit.agents.cli.log.setup_logging`, which does `root.addHandler(handler)`
# (read in the pinned source, not inferred). Every vendor record the MAIN
# process logs after that - registration, drains, the pooler warnings, the ones
# my own comment listed as the reason for the call - reached that handler
# unmasked.
#
# The job path passed because `proc_main` attaches its handler BEFORE `prewarm`
# runs. That is luck, not design: a mask that works only when it happens to be
# installed second is not a mask. So these cases fix the ORDER, not one call
# site, and they assert on handler output with the handler arriving LAST.


@pytest.fixture(autouse=True)
def _restore_record_factory():
    """This module installs process-wide logging state, so put it back.

    A test here must not get to decide what a test elsewhere sees.
    """
    previous = logging.getLogRecordFactory()
    try:
        yield
    finally:
        logging.setLogRecordFactory(previous)


@pytest.fixture
def production_order():
    """Root with NO handlers, and a way to add one afterwards.

    `clear()` has to be called from the TEST BODY, not from this fixture:
    pytest's logging plugin attaches its own root handlers at the start of the
    CALL phase, after fixtures have run. My first draft cleared during setup, so
    by the time the test called `install_vendor_log_mask()` pytest's handlers
    were back, the install found them, and the handler filter masked the record IN
    PLACE before my own handler ever saw it. Two order cases passed against the
    unfixed code. The premise of an order test is the order, so the test body
    owns it.
    """

    class Order:
        def __init__(self) -> None:
            self._root = logging.getLogger()
            self._removed: list[logging.Handler] = []
            self._level = self._root.level
            self._added: list[logging.Handler] = []
            self.stream = io.StringIO()

        def clear(self) -> None:
            """Root as `import adapter.agent` finds it in the main process."""
            for handler in self._root.handlers[:]:
                self._root.removeHandler(handler)
                self._removed.append(handler)

        def add_handler(self) -> io.StringIO:
            """What `cli.run_app` -> `setup_logging` does, one step later."""
            handler = logging.StreamHandler(self.stream)
            handler.setFormatter(
                logging.Formatter("%(name)s %(levelname)s %(message)s")
            )
            self._root.addHandler(handler)
            self._root.setLevel(logging.WARNING)
            self._added.append(handler)
            return self.stream

        def restore(self) -> None:
            for handler in self._added:
                self._root.removeHandler(handler)
            for handler in self._removed:
                self._root.addHandler(handler)
            self._root.setLevel(self._level)

    order = Order()
    try:
        yield order
    finally:
        order.restore()


@pytest.mark.parametrize(
    "logger_name",
    (
        # The exact logger whose warning carried the account id.
        "livekit.agents.voice.agent_activity",
        "openai",
    ),
)
def test_a_vendor_line_is_masked_when_the_handler_arrives_after_the_install(
    logger_name: str, production_order
) -> None:
    from adapter.logmask import install_vendor_log_mask

    production_order.clear()
    install_vendor_log_mask()  # main process: root has no handlers yet
    stream = production_order.add_handler()  # cli.run_app -> setup_logging

    logging.getLogger(logger_name).warning(_upstream_body())

    output = stream.getvalue()
    for marker in (USER_ID, EMAIL, TOKEN, "NOTAREALPASSWORD", TRANSCRIPT):
        assert marker not in output, marker
    assert "insufficient_quota" in output


def test_the_handler_cli_run_app_installs_is_covered(capsys) -> None:
    """Not a stand-in handler: the function `run_app` actually calls.

    The order bug was invisible to a test that added its own handler first, so
    this one hands the job to the framework and reads its stdout.
    """
    # The only livekit import in this file: the core-only gate installs no
    # voice group, and an unguarded import would ERROR there rather than skip.
    log = pytest.importorskip(
        "livekit.agents.cli.log", reason="voice dependency group not installed"
    )

    from adapter.logmask import install_vendor_log_mask

    NOISY_LOGGERS, setup_logging = log.NOISY_LOGGERS, log.setup_logging

    root = logging.getLogger()
    existing, level = root.handlers[:], root.level
    noisy = {name: logging.getLogger(name).level for name in NOISY_LOGGERS}
    for handler in existing:
        root.removeHandler(handler)
    try:
        install_vendor_log_mask()
        setup_logging("WARNING", False, False)
        logging.getLogger("livekit.agents.voice.agent_activity").warning(
            _upstream_body()
        )
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in existing:
            root.addHandler(handler)
        root.setLevel(level)
        for name, restored in noisy.items():
            logging.getLogger(name).setLevel(restored)

    output = capsys.readouterr().out
    for marker in (USER_ID, EMAIL, TOKEN, "NOTAREALPASSWORD", TRANSCRIPT):
        assert marker not in output, marker
    assert "insufficient_quota" in output


def test_installing_twice_leaves_one_mask_in_the_chain() -> None:
    """`prewarm` runs per process and `main` once per start, and a stacked
    wrapper would mask a masked line - the diagnosis-into-markers failure the
    `message` field already taught me."""
    from adapter.logmask import install_vendor_log_mask

    install_vendor_log_mask()
    installed = logging.getLogRecordFactory()
    install_vendor_log_mask()

    assert installed is not logging.LogRecord, "the mask was never installed"
    assert logging.getLogRecordFactory() is installed


def test_a_record_factory_someone_else_installed_still_runs(
    production_order,
) -> None:
    """We wrap the previous factory, never replace it: the framework's own, or a
    test harness's, keeps running."""
    from adapter.logmask import install_vendor_log_mask

    previous = logging.getLogRecordFactory()

    def stamping(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.stamped_by_someone_else = True
        return record

    production_order.clear()
    logging.setLogRecordFactory(stamping)
    install_vendor_log_mask()
    stream = production_order.add_handler()

    seen: dict[str, logging.LogRecord] = {}

    class Capture(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            seen["record"] = record
            return True

    logging.getLogger().handlers[-1].addFilter(Capture())
    logging.getLogger("openai").warning(_upstream_body())

    assert getattr(seen["record"], "stamped_by_someone_else", False) is True
    assert USER_ID not in stream.getvalue()


def test_our_own_records_are_untouched_by_the_process_wide_hook(
    production_order,
) -> None:
    """A guard, not a fix: it held before this change and has to keep holding.

    A record factory is process-wide, so it now sees OUR records too. `EventLog`
    classifies every field it emits and is the real guarantee; a second mask
    over our own stream would redact a field we deliberately kept.
    """
    from adapter.logmask import install_vendor_log_mask

    production_order.clear()
    install_vendor_log_mask()
    stream = production_order.add_handler()

    logging.getLogger("ambassador.events").warning(
        "lead_store_connected host=%s user=%s",
        "aws-1-eu-central-1.pooler.supabase.com:5432",
        USER_ID,
    )

    assert USER_ID in stream.getvalue()


# ---------------------------------------------------------------------------
# An echoed payload ends at an UNESCAPED delimiter. CodeRabbit found this on
# #133 and it is real: `(.*?)(\2)` stops at the first quote character it sees,
# and a buyer saying "it's" arrives inside the vendor's repr as `it\'s`. The
# rule masked four characters and left the rest of the sentence in the clear -
# the exact leak it exists to stop, one apostrophe away.


def test_an_escaped_quote_does_not_end_the_echoed_payload_early() -> None:
    from adapter.logmask import mask_text

    body = (
        "OpenAI API error: {'error': {'metadata': {'echoed_prompt': "
        "'turn 3 buyer: it\\'s about two million dirhams'}}}"
    )

    masked = mask_text(body)

    assert "two million dirhams" not in masked
    assert "about" not in masked
    # Still idempotent, which is what lets the mask run in both processes.
    assert mask_text(masked) == masked


def test_the_same_holds_for_a_double_quoted_payload() -> None:
    """The vendors use both quote styles, sometimes in one body."""
    from adapter.logmask import mask_text

    body = (
        'API error: {"echoed_prompt": "turn 3 buyer: she said \\"two million\\" flat"}'
    )

    masked = mask_text(body)

    assert "two million" not in masked
    assert "flat" not in masked
