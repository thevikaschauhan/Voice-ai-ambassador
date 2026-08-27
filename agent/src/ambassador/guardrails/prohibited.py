"""Prohibited-language validator (docs/03- validator 2).

Patterns live in data/prohibited-patterns.yaml - language-neutral data,
reviewable by a non-engineer. English-only in the POC, and that is disclosed;
non-English patterns must be written by a native speaker, never translated
by the build agent.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"


@dataclass(frozen=True)
class ProhibitedPattern:
    category: str
    language: str
    regex: re.Pattern


def load_patterns(path: Path | None = None) -> list[ProhibitedPattern]:
    raw = yaml.safe_load(
        (path or _DATA_DIR / "prohibited-patterns.yaml").read_text(encoding="utf-8")
    )
    compiled: list[ProhibitedPattern] = []
    for group in raw:
        for pattern in group["patterns"]:
            compiled.append(
                ProhibitedPattern(
                    category=group["category"],
                    language=group["language"],
                    regex=re.compile(pattern, re.IGNORECASE),
                )
            )
    return compiled


def check_prohibited(
    text: str, patterns: list[ProhibitedPattern]
) -> list[str]:
    """Return 'category: matched text' for each hit. Empty list = pass."""
    hits: list[str] = []
    for p in patterns:
        m = p.regex.search(text)
        if m:
            hits.append(f"{p.category}: {m.group(0)!r}")
    return hits
