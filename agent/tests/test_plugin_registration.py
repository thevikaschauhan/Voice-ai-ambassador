"""Framework plugins must be registered on the main thread, and this suite was
blind to the whole class until a live session found it.

`livekit/agents/plugin.py` raises `RuntimeError("Plugins must be registered on
the main thread")` if a plugin registers from anywhere else, and a plugin
registers as a SIDE EFFECT OF BEING IMPORTED. So a `from livekit.plugins import
x` inside a function body is not a style choice - it is a thread-affinity bug
waiting for that function to be called off the main thread, which is exactly
what console mode does with the job.

`stt_factory.build_stt` held the only such import in `src/adapter/`, and #46
flipped the default onto it: from that commit, `uv run python -m adapter.agent
console` crashed after the agent had fully constructed - last event on the
stream `lexicon`, no `stt_enabled` - while `STT_PROVIDER=openrouter` still
booted through to `session_end`, because that recogniser is our own class and
registers no plugin. Console mode is the venue plan B (docs/06-) and the day-1
gate path.

Nothing in 747 passing tests saw it, for one reason worth stating: every test
runs on the main thread, and any test that builds the Deepgram node there
caches the module, so an in-process check would pass vacuously afterwards. Both
tests below are built to survive that - one runs in a fresh interpreter, the
other reads the source.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ADR-002: the core stays installable and testable with no voice stack present.
pytest.importorskip("livekit.agents", reason="voice dependency group not installed")

ADAPTER = Path(__file__).resolve().parents[1] / "src" / "adapter"

# One line of proof that a job really does run off the main thread, quoted from
# the framework rather than asserted: `cli._run_tcp_console` drives the session
# from a worker thread, which is why this class of bug exists at all.
_OFF_MAIN_THREAD = """
import threading
from pathlib import Path

from adapter.config import load_settings
from adapter.stt_factory import build_stt

settings = load_settings(Path("/nonexistent/.env"))
settings = type(settings)(
    **{
        **settings.redacted(),
        "stt_enabled": True,
        "stt_provider": "deepgram",
        "deepgram_api_key": "not-a-real-credential",
    }
)

box = {}


def work():
    try:
        box["node"] = build_stt(settings)
    except BaseException as exc:  # RuntimeError is what we are hunting
        box["err"] = f"{type(exc).__name__}: {exc}"


thread = threading.Thread(target=work)
thread.start()
thread.join()

if "err" in box:
    raise SystemExit(box["err"])
print(type(box["node"]).__name__)
"""


def test_the_recogniser_can_be_built_off_the_main_thread():
    """The regression, reproduced the way console mode hits it.

    A FRESH interpreter, deliberately: run in-process and whichever test built
    the Deepgram node on the main thread first would have cached the module and
    made this pass for the wrong reason. Costs about a second, needs no
    credentials and touches no network - `deepgram.STT(...)` only constructs.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_OFF_MAIN_THREAD)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "building the recogniser off the main thread failed, which is what "
        "console mode does with the job:\n" + (result.stderr or result.stdout)
    )
    assert "STT" in result.stdout, result.stdout


def _lazy_plugin_imports() -> list[str]:
    """Every `livekit.plugins` import that is not at module scope.

    Reads the source rather than the runtime, so it is immune to import
    ordering and to a plugin some other test already registered.
    """
    found: list[str] = []
    for path in sorted(ADAPTER.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                module = None
                if isinstance(inner, ast.ImportFrom):
                    module = inner.module
                elif isinstance(inner, ast.Import):
                    module = next(
                        (a.name for a in inner.names if "livekit.plugins" in a.name),
                        None,
                    )
                if module and module.startswith("livekit.plugins"):
                    found.append(f"{path.name}:{inner.lineno} in {node.name}()")
    return found


def test_the_discovery_reads_the_adapter_it_claims_to_read():
    """A scanner that scans nothing passes everything."""
    files = list(ADAPTER.glob("*.py"))
    assert len(files) >= 10, [p.name for p in files]
    assert any(p.name == "stt_factory.py" for p in files)


def test_no_plugin_is_imported_inside_a_function():
    """The class, not the instance.

    Importing a `livekit.plugins.*` module registers a plugin, and registration
    outside the main thread raises. At module scope that happens once, on
    whichever thread first imports the adapter - the main one, at worker boot.
    Inside a function it happens on whatever thread calls the function, and
    console mode calls into the adapter from a job thread.

    Every other plugin in this adapter is already imported at module scope
    (fishaudio, openai, silero); the Deepgram one was the exception, and #46
    made it the default path. If a lazy import is ever genuinely needed, do it
    from `prewarm` where the main thread is guaranteed - and change this test
    deliberately rather than deleting it.
    """
    lazy = _lazy_plugin_imports()
    assert not lazy, (
        "these livekit.plugins imports run on whatever thread calls the "
        "function, and plugin registration off the main thread raises: " + str(lazy)
    )
