"""The hosted event stream's recovery path, at the four seams that broke it."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import railway_events  # noqa: E402

# What Railway renders for a line the agent printed as JSON. This is the whole
# reason the tool exists: it is not JSON any more.
FLATTENED = (
    '2026-09-02T13:40:58.060209820Z [INFO]  ts="2026-09-02T13:40:50.924+00:00" '
    'session="sess_8a98e771e1e3" event="disclosure" language="en"'
)

# The same line through `--json`: our fields back, with their real types, plus
# the three attributes Railway adds.
AS_JSON = json.dumps(
    {
        "uncertified_fallback": False,
        "level": "info",
        "language": "en",
        "message": "",
        "ts": "2026-09-02T13:40:50.924+00:00",
        "session": "sess_8a98e771e1e3",
        "timestamp": "2026-09-02T13:40:58.060209820Z",
        "event": "disclosure",
    }
)


def test_the_plain_log_is_not_recoverable():
    """Plain `railway logs` flattens our JSON to key="value" text. A run
    pointed at it measures nothing, and the failure looks like the agent never
    spoke rather than like a parse problem - which cost one whole run."""
    assert railway_events.translate(FLATTENED) is None


def test_json_mode_gives_the_record_back_with_its_types():
    record = railway_events.translate(AS_JSON)
    assert record is not None
    assert record["event"] == "disclosure"
    # A real bool, not the string "false": the flattened form could not say this.
    assert record["uncertified_fallback"] is False
    # Railway's own attributes are gone, so what is left is what the agent wrote.
    for key in railway_events.RAILWAY_KEYS:
        assert key not in record


def test_key_order_does_not_defeat_the_dedupe():
    """The bug that corrupted the first hosted run. Railway does not preserve
    key order between deliveries of the same line, so comparing serialised text
    without sorting let every replayed copy through - 5170 lines for 125
    events, and a second turn 1 that put every later clip out of phase."""
    first = json.dumps({"event": "user_turn", "turn": 1, "ts": "2026-09-02T13:00:00+00:00"})
    same_reordered = json.dumps({"ts": "2026-09-02T13:00:00+00:00", "turn": 1, "event": "user_turn"})
    assert first != same_reordered
    records = list(railway_events.stream(iter([first, same_reordered])))
    assert len(records) == 1


def test_a_reconnects_replay_of_an_older_session_is_cut_off():
    """The CLI's stream drops and replays on reconnect. Without a cutoff on the
    agent's own ts, a previous session's turn numbers arrive as this run's."""
    old = json.dumps({"event": "user_turn", "turn": 1, "ts": "2026-09-02T13:00:00+00:00"})
    new = json.dumps({"event": "user_turn", "turn": 1, "ts": "2026-09-02T14:00:00+00:00"})
    kept = list(railway_events.stream(iter([old, new]), since="2026-09-02T13:30:00"))
    assert [r["ts"] for r in kept] == ["2026-09-02T14:00:00+00:00"]


def test_framework_log_lines_are_not_events():
    """The stream carries the framework's own structured logs too, and they are
    not turns."""
    framework = json.dumps({"level": "info", "message": "registered worker", "id": "AW_x"})
    assert railway_events.translate(framework) is None
