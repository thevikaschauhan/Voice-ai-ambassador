"""Inventory: load, validate, derive, serialise.

The one place facts enter the system (invariant 1) and the one place derived
figures are computed (invariant 2). Derived figures are computed, never
hand-authored: a computed figure cannot be mistyped, an authored one can.
"""

import json
from pathlib import Path

import yaml

from .schemas import AllowedFigures, DerivedFigures, Project

# Repo layout: <repo>/agent/src/ambassador/inventory.py -> <repo>/data
# Holds for the editable install this POC always uses.
DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def load_inventory(path: Path | None = None) -> list[Project]:
    raw = json.loads((path or DATA_DIR / "inventory.json").read_text(encoding="utf-8"))
    projects = [Project.model_validate(entry) for entry in raw]
    ids = [p.id for p in projects]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate project ids in inventory")
    return projects


def derive(project: Project) -> DerivedFigures | None:
    """Milestone amounts from the source price and plan. Pure."""
    if project.price_from_aed is None or not project.payment_plan:
        return None
    return DerivedFigures(
        milestone_amounts_aed=[
            int(round(project.price_from_aed * m.pct / 100))
            for m in project.payment_plan
        ]
    )


def _load_whitelist(path: Path | None = None) -> dict:
    data = yaml.safe_load(
        (path or DATA_DIR / "whitelist.yaml").read_text(encoding="utf-8")
    )
    for section in ("amounts", "percents", "years"):
        for entry in data.get(section) or []:
            if not entry.get("why"):
                raise ValueError(
                    f"whitelist entry {entry.get('value')!r} in {section} has no "
                    "'why' - every whitelist entry is a hole a wrong figure could "
                    "pass through and must justify itself"
                )
    return data


def build_allowed_figures(
    projects: list[Project], whitelist_path: Path | None = None
) -> AllowedFigures:
    """Global allowed set (ADR-008): every figure in inventory, source and
    computed, plus the justified whitelist."""
    amounts: set[float] = set()
    percents: set[float] = set()
    years: set[int] = set()

    for p in projects:
        if p.price_from_aed is not None:
            amounts.add(float(p.price_from_aed))
        for size in (p.size_sqft_min, p.size_sqft_max):
            if size is not None:
                amounts.add(float(size))
        if p.handover is not None:
            years.add(p.handover.year)
        if p.payment_plan is not None:
            percents.update(float(m.pct) for m in p.payment_plan)
        derived = derive(p)
        if derived is not None:
            amounts.update(float(a) for a in derived.milestone_amounts_aed)

    wl = _load_whitelist(whitelist_path)
    amounts.update(float(e["value"]) for e in wl.get("amounts") or [])
    percents.update(float(e["value"]) for e in wl.get("percents") or [])
    years.update(int(e["value"]) for e in wl.get("years") or [])

    return AllowedFigures(
        amounts=frozenset(amounts),
        percents=frozenset(percents),
        years=frozenset(years),
    )


def serialise_for_prompt(projects: list[Project]) -> str:
    """One compact line per project, derived figures inline so the model never
    needs to compute them."""
    lines: list[str] = []
    for p in projects:
        if p.status == "branded_enquiry":
            lines.append(
                f"- {p.name} ({p.id}) | {p.area} | branded collection | "
                f"price on enquiry only - never state a figure, a range, or a "
                f"comparison; offer the human ambassador | "
                f"units: {', '.join(p.unit_types)}"
            )
            continue
        parts = [f"- {p.name} ({p.id})", p.area, p.status]
        if p.price_from_aed is not None:
            parts.append(f"from AED {p.price_from_aed:,}")
        parts.append(f"units: {', '.join(p.unit_types)}")
        if p.size_sqft_min is not None and p.size_sqft_max is not None:
            parts.append(f"{p.size_sqft_min}-{p.size_sqft_max} sqft")
        if p.handover is not None:
            parts.append(f"handover Q{p.handover.quarter} {p.handover.year}")
        derived = derive(p)
        if p.payment_plan is not None and derived is not None:
            plan = "; ".join(
                f"{m.label} {m.pct:g}% = AED {amt:,}"
                for m, amt in zip(
                    p.payment_plan, derived.milestone_amounts_aed, strict=True
                )
            )
            parts.append(f"plan: {plan}")
        if p.amenities:
            parts.append(f"amenities: {', '.join(p.amenities)}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
