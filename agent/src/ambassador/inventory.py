"""Inventory: load, validate, derive, serialise.

The one place facts enter the system (invariant 1) and the one place derived
figures are computed (invariant 2). Derived figures are computed, never
hand-authored: a computed figure cannot be mistyped, an authored one can.
"""

import json
import re
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


def resolve_project_id(value: str, projects: list[Project] | None = None) -> str | None:
    """A model-supplied id OR name to an inventory id, or None.

    Deterministic - a lookup against the records we ship, never a guess and
    never a second model call. It lives here because inventory owns the id
    vocabulary: the adapter should not be the place that knows "Binghatti
    Skyrise" and `binghatti-skyrise` are the same project.

    Both sides are normalised the same way (casefolded, runs of non-alphanumeric
    characters to a single dash), so a name matches its own slug and a model
    that lower-cased or double-spaced a name has not made a different claim.
    Names are matched too, not just slugs, because `Bugatti Residences by
    Binghatti` slugs to something that is not its id.
    """
    projects = load_inventory() if projects is None else projects
    wanted = _slug(value)
    if not wanted:
        return None
    for project in projects:
        if wanted in (_slug(project.id), _slug(project.name)):
            return project.id
    return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


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


# Only `currency` figures may take a spoken form that names a currency.
# `identifier` is read as a sequence and must never take a quantity form.
_WHITELIST_KINDS = frozenset({"currency", "quantity", "identifier"})


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
            if entry.get("kind") not in _WHITELIST_KINDS:
                raise ValueError(
                    f"whitelist entry {entry.get('value')!r} in {section} has "
                    f"kind {entry.get('kind')!r}, not one of "
                    f"{'/'.join(sorted(_WHITELIST_KINDS))}. The kind decides "
                    "whether verbalisation may give it a currency-naming spoken "
                    "form; guessing wrong makes a phone number a price or a "
                    "square footage a sum of money."
                )
    return data


def build_allowed_figures(
    projects: list[Project], whitelist_path: Path | None = None
) -> AllowedFigures:
    """Global allowed set (ADR-008): every figure in inventory, source and
    computed, plus the justified whitelist."""
    amounts: set[float] = set()
    currency: set[float] = set()
    identifiers: set[float] = set()
    percents: set[float] = set()
    years: set[int] = set()

    for p in projects:
        if p.price_from_aed is not None:
            amounts.add(float(p.price_from_aed))
            currency.add(float(p.price_from_aed))
        for size in (p.size_sqft_min, p.size_sqft_max):
            if size is not None:
                amounts.add(float(size))
        if p.handover is not None:
            years.add(p.handover.year)
        if p.payment_plan is not None:
            percents.update(float(m.pct) for m in p.payment_plan)
        derived = derive(p)
        if derived is not None:
            # Payment-plan instalments: money by construction.
            amounts.update(float(a) for a in derived.milestone_amounts_aed)
            currency.update(float(a) for a in derived.milestone_amounts_aed)

    wl = _load_whitelist(whitelist_path)
    amounts.update(float(e["value"]) for e in wl.get("amounts") or [])
    currency.update(
        float(e["value"]) for e in wl.get("amounts") or [] if e["kind"] == "currency"
    )
    # Identifiers come only from the whitelist. Nothing in inventory is read as
    # a sequence - a price, a size and a handover year are all quantities - so
    # there is no inventory branch to add here, and if one ever appears it will
    # arrive as a whitelist entry like the hotline did.
    identifiers.update(
        float(e["value"]) for e in wl.get("amounts") or [] if e["kind"] == "identifier"
    )
    percents.update(float(e["value"]) for e in wl.get("percents") or [])
    years.update(int(e["value"]) for e in wl.get("years") or [])

    return AllowedFigures(
        amounts=frozenset(amounts),
        currency_amounts=frozenset(currency),
        identifiers=frozenset(identifiers),
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
