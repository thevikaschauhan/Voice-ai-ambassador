"""The hosted worker's event stream, recovered from Railway's logs.

`buyer_publisher.py` reads the agent's own `AMBASSADOR_EVENT_LOG`. On Railway
that file is inside the container, and `railway logs` is not a substitute for
it: Railway PARSES every JSON line the agent prints and re-renders it as
flattened `key="value"` text, which no longer parses as JSON at all. A run
pointed at that output measures nothing and times out waiting for a disclosure
that did arrive.

`railway logs -s <service> -e <env> -d --json` gives the attributes back with
their real types (`uncertified_fallback: false`, not `"false"`), so the original
record is recoverable by dropping the three keys Railway adds itself. Usage:

    railway logs -s <service> -e production -d --json \
      | python tools/railway_events.py events.jsonl --since 2026-09-02T13:51:10

THIS IS FOR ANALYSIS AND NOT A CLOCK. Measured on the first hosted run, the lag
from an event's own `ts` to its arrival here was p50 6.3s, p90 85.6s, max 99.5s
(n=162). Turn-scoped waits and the barge-in trigger cannot be paced off it - a
barge-in fired 0.6s after a `tts_first_audio` that arrived 6 seconds late lands
on the following turn, which is how one run asked for 2 interruptions and got 4.
The per-turn figures inside the records are computed in the agent and are
unaffected by the lag; only the pacing is.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterator

# Railway's own attributes, which are not ours. No emitted event carries a field
# of these names (checked against every `emit()` in adapter/), so dropping them
# shadows nothing.
RAILWAY_KEYS = ("level", "message", "timestamp")


def dedupe_key(record: dict[str, Any]) -> str:
    """Order-insensitive identity for one record.

    `sort_keys` is load-bearing. The CLI's stream drops often and a reconnect
    REPLAYS recent lines, so duplicates are the normal case; and Railway does
    not preserve key order between deliveries of the same line, so comparing
    serialised text without sorting lets every replayed copy through. That is
    not a tidiness problem - a replayed `user_turn` gives the harness a second
    turn 1 and puts every later clip out of phase.
    """
    return json.dumps(record, sort_keys=True)


def translate(line: str, since: str = "") -> dict[str, Any] | None:
    """One `--json` line to one agent event record, or None to skip it.

    `since` compares against the agent's own `ts` (ISO-8601, so lexicographic
    order is chronological). A reconnect replays history, and without a cutoff a
    previous session's turn numbers arrive as if they were this run's.
    """
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or "event" not in record:
        return None
    if since and str(record.get("ts", "")) < since:
        return None
    for key in RAILWAY_KEYS:
        record.pop(key, None)
    return record


def stream(lines: Iterator[str], since: str = "") -> Iterator[dict[str, Any]]:
    """Translated, deduplicated records, in arrival order."""
    seen: set[str] = set()
    for line in lines:
        record = translate(line, since)
        if record is None:
            continue
        key = dedupe_key(record)
        if key in seen:
            continue
        seen.add(key)
        yield record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", help="file to append the recovered event lines to")
    parser.add_argument(
        "--since",
        default="",
        help="drop records whose own ts is older than this ISO timestamp",
    )
    args = parser.parse_args(argv)
    with open(args.out, "a", encoding="utf-8") as handle:
        for record in stream(sys.stdin, args.since):
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
