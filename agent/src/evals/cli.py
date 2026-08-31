"""`uv run eval` - run the matrix, print the rates, write the report.

Offline by default: no key, no spend, no network, so it runs in CI on every
commit and a prompt change cannot land unmeasured (docs/05-).

    uv run eval                          # the whole matrix, offline
    uv run eval --category prompt_injection guarantee_pressure
    uv run eval --live --category grounding_leading_question
    uv run eval --out docs/eval-report.md

Live mode is deliberately awkward to run wide: it needs `--live` AND at least
one `--category`, because the failure mode of a convenient live flag is a
full-matrix run nobody meant to pay for.

Exit status is 1 when a gated category fails, so CI and the demo checklist read
the same signal.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from adapter.config import load_settings

from .backends import BackendError, FixtureBackend, LiveBackend, ModelBackend
from .cases import CASES_DIR, EvalCase, load_cases, load_categories
from .report import (
    CategoryResult,
    build_suite,
    evaluate,
    render_console,
    render_markdown,
)
from .runner import Harness, run_case

# <repo>/agent/src/evals/cli.py -> <repo>/docs
DEFAULT_REPORT = Path(__file__).resolve().parents[3] / "docs" / "eval-report.md"


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval", description="Run the Binghatti ambassador eval matrix."
    )
    parser.add_argument(
        "--category",
        nargs="+",
        default=None,
        metavar="KEY",
        help="only these categories (default: all of them)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "call the real model instead of replaying fixtures. Costs money and "
            "requires --category, so a wide run has to be asked for on purpose."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"report path (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--no-report", action="store_true", help="print the table, write nothing"
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=CASES_DIR,
        help=argparse.SUPPRESS,  # tests point this at a fixture tree
    )
    return parser.parse_args(argv)


def _selected(cases: list[EvalCase], keys: Sequence[str] | None) -> list[EvalCase]:
    if keys is None:
        return cases
    wanted = set(keys)
    return [case for case in cases if case.category in wanted]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    table = load_categories(args.cases_dir / "categories.yaml")
    specs = table.by_key()
    cases = load_cases(args.cases_dir)

    unknown = sorted({c.category for c in cases} - set(specs))
    if unknown:
        print(
            f"error: cases declare categories not in categories.yaml: "
            f"{', '.join(unknown)}",
            file=sys.stderr,
        )
        return 2
    if args.category:
        missing = sorted(set(args.category) - set(specs))
        if missing:
            print(
                f"error: unknown category: {', '.join(missing)}. Known: "
                f"{', '.join(sorted(specs))}",
                file=sys.stderr,
            )
            return 2
    if args.live and not args.category:
        print(
            "error: --live requires --category. A live run spends money per case; "
            "name the categories you mean to pay for.",
            file=sys.stderr,
        )
        return 2

    harness = Harness.load()
    backend: ModelBackend
    if args.live:
        settings = load_settings()
        try:
            backend = LiveBackend(
                api_key=settings.openrouter_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                thinking_disabled=settings.thinking_disabled,
            )
        except BackendError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        model_label = settings.llm_model
    else:
        backend = FixtureBackend()
        model_label = "fixtures (no model called)"

    selected = _selected(cases, args.category)
    results_by_category: dict[str, list] = {key: [] for key in specs}
    for case in selected:
        observed = run_case(case, harness, backend)
        results_by_category[case.category].append(evaluate(case, observed))

    shown = (
        [specs[key] for key in args.category] if args.category else table.categories
    )
    categories = [
        CategoryResult(spec=spec, results=tuple(results_by_category[spec.key]))
        for spec in shown
    ]
    suite = build_suite(
        mode=backend.name,
        model=model_label,
        prompt_fingerprint=harness.prompt_fingerprint(),
        categories=categories,
    )
    print(render_console(suite))

    if not args.no_report:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_markdown(suite), encoding="utf-8")
        print(f"\nreport written to {args.out}")

    if isinstance(backend, LiveBackend):
        print(f"live calls made: {backend.calls}")
        backend.close()

    return 0 if suite.gates_held else 1


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
