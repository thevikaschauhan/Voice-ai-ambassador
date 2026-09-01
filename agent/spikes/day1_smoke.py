"""Day 1 smoke gates (docs/06-): one round-trip through all three vendors.

  A. LLM  (ADR-016): qwen3.7-flash via OpenRouter, thinking OFF, real inventory
     prompt. Gate: TTFT 200-600ms, zero reasoning tokens. Run twice for cache.
  B. TTS  (ADR-014): Fish s2.1-pro, English voice. Gate: time to first audio
     byte inside 75-300ms budget line.
  C. STT  (ADR-015): the audio from B, base64, through OpenRouter
     /audio/transcriptions on qwen3-asr-1.7b. Gate: latency near the 100-300ms
     line and a sane round-trip transcript ("Binghatti", "975").

Rerunnable: uv run python spikes/day1_smoke.py
Never prints secrets.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR / "src"))

from ambassador.inventory import load_inventory, serialise_for_prompt  # noqa: E402
from ambassador.prompts import build_ambassador_prompt  # noqa: E402

OUT_DIR = Path(os.environ.get("SPIKE_OUT_DIR", "/tmp"))


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (AGENT_DIR / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.split("#")[0].strip()
    for k in list(
        env
    ):  # process env overrides .env, e.g. FISH_TTS_MODEL for a free-tier run
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def gate(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def test_llm(env: dict[str, str]) -> bool:
    print("\n=== A. LLM gate (ADR-016) ===")
    projects = load_inventory()
    system = build_ambassador_prompt(serialise_for_prompt(projects), "en")
    body = {
        "model": env["LLM_MODEL"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning": {"enabled": False},
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "What does a studio at Binghatti Skyrise cost?",
            },
        ],
    }
    headers = {"Authorization": f"Bearer {env['OPENROUTER_API_KEY']}"}
    url = f"{env['LLM_BASE_URL']}/chat/completions"

    ok_all = True
    for attempt in ("cold", "warm"):
        t0 = time.perf_counter()
        ttft = first_sentence = None
        text, usage = "", {}
        with httpx.stream("POST", url, headers=headers, json=body, timeout=60) as r:
            if r.status_code == 429:
                r.read()
                print("  429 upstream rate limit - waiting 15s and retrying (up to 4x)")
                for _ in range(4):
                    time.sleep(15)
                    t0 = time.perf_counter()
                    retry = httpx.post(
                        url, headers=headers, json={**body, "stream": False}, timeout=60
                    )
                    if retry.status_code == 200:
                        print(
                            "  retry succeeded non-streaming; rerun script for streaming timings"
                        )
                        break
                    print(f"  still {retry.status_code}")
                else:
                    return False
                continue
            if r.status_code != 200:
                r.read()
                print(f"  HTTP {r.status_code}: {r.text[:300]}")
                return False
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                usage = chunk.get("usage") or usage
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                piece = delta.get("content") or ""
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    text += piece
                    if first_sentence is None and re.search(r"[.!?؟]", text):
                        first_sentence = time.perf_counter() - t0
        total = time.perf_counter() - t0
        details = usage.get("completion_tokens_details") or {}
        reasoning_toks = details.get("reasoning_tokens", 0) or 0
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        print(
            f"  [{attempt}] ttft={ttft * 1000:.0f}ms  first_sentence="
            f"{(first_sentence or total) * 1000:.0f}ms  total={total:.2f}s  "
            f"prompt_tokens={usage.get('prompt_tokens')} (cached={cached})  "
            f"reasoning_tokens={reasoning_toks}"
        )
        print(f"  reply: {text[:160]!r}")
        ok_all &= gate(
            "thinking off", reasoning_toks == 0, f"{reasoning_toks} reasoning tokens"
        )
        if attempt == "warm":
            ok_all &= gate(
                "TTFT <= 600ms (warm)",
                ttft is not None and ttft <= 0.6,
                f"{ttft * 1000:.0f}ms",
            )
    return ok_all


def test_tts(env: dict[str, str]) -> tuple[bool, Path | None]:
    print("\n=== B. TTS gate (ADR-014) ===")
    body = {
        "text": "Welcome to Binghatti. The studio starts at 975,000 dirhams.",
        "format": "mp3",
        "latency": "low",
    }
    if env.get("TTS_VOICE_ID_EN"):
        body["reference_id"] = env["TTS_VOICE_ID_EN"]
    headers = {
        "Authorization": f"Bearer {env['FISH_API_KEY']}",
        "Content-Type": "application/json",
        "model": env.get("FISH_TTS_MODEL", "s2.1-pro"),
    }
    t0 = time.perf_counter()
    ttfb = None
    audio = b""
    with httpx.stream(
        "POST", "https://api.fish.audio/v1/tts", headers=headers, json=body, timeout=60
    ) as r:
        if r.status_code != 200:
            r.read()
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
            return False, None
        for chunk in r.iter_bytes():
            if chunk and ttfb is None:
                ttfb = time.perf_counter() - t0
            audio += chunk
    total = time.perf_counter() - t0
    out = OUT_DIR / "day1_tts_en.mp3"
    out.write_bytes(audio)
    print(
        f"  first_audio_byte={ttfb * 1000:.0f}ms  total={total:.2f}s  bytes={len(audio)}  saved={out}"
    )
    ok = gate(
        "TTS first byte <= 300ms",
        ttfb is not None and ttfb <= 0.3,
        f"{ttfb * 1000:.0f}ms",
    )
    gate(
        "audio produced",
        len(audio) > 10_000,
        f"{len(audio)} bytes (listen to it for 'Binghatti')",
    )
    return ok, out


def test_stt(env: dict[str, str], audio_path: Path) -> bool:
    print("\n=== C. STT gate (ADR-015) ===")
    b64 = base64.b64encode(audio_path.read_bytes()).decode()
    body = {
        "model": env["STT_MODEL_DEFAULT"],
        "input_audio": {"data": b64, "format": audio_path.suffix.lstrip(".")},
        "language": "en",
    }
    headers = {"Authorization": f"Bearer {env['OPENROUTER_API_KEY']}"}
    t0 = time.perf_counter()
    r = httpx.post(
        "https://openrouter.ai/api/v1/audio/transcriptions",
        headers=headers,
        json=body,
        timeout=60,
    )
    latency = time.perf_counter() - t0
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:300]}")
        return False
    data = r.json()
    text = data.get("text", "")
    print(f"  latency={latency * 1000:.0f}ms  usage={data.get('usage')}")
    print(f"  transcript: {text!r}")
    ok = gate(
        "round-trip transcript sane",
        "binghatti" in text.lower() and "975" in text.replace(",", ""),
        "brand name + figure survived TTS->STT",
    )
    gate(
        "STT latency (info only - includes upload)",
        latency <= 1.0,
        f"{latency * 1000:.0f}ms",
    )
    return ok


def main() -> int:
    env = load_env()
    only = {s for s in os.environ.get("SPIKE_ONLY", "llm,tts,stt").split(",")}
    results = {}
    if "llm" in only:
        results["llm"] = test_llm(env)
    audio = None
    if "tts" in only:
        tts_ok, audio = test_tts(env)
        results["tts"] = tts_ok
    if "stt" in only:
        if audio is None and os.environ.get("SPIKE_STT_AUDIO"):
            audio = Path(os.environ["SPIKE_STT_AUDIO"])
            print(f"\n  (STT gate runs on fallback audio: {audio.name})")
        results["stt"] = test_stt(env, audio) if audio else False
    print("\n=== Summary ===")
    for k, v in results.items():
        print(f"  {k.upper()}: {'PASS' if v else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
