"""System prompts. The ambassador prompt is the first line of defence; the
guardrail pipeline is the line that holds (ADR-007). The naive prompt exists
for the defence-in-depth demonstration (docs/03- demo modes) and must never
ship outside DEMO contexts.
"""

from collections.abc import Mapping
from typing import get_args

from .schemas import Language

LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "ar": "Arabic", "hi": "Hindi"}


def _require_every_language_named(names: Mapping[str, str]) -> None:
    """A hand-keyed table of the Literal has to be checked, not trusted.

    The endonym cannot be derived from the language code, so this table is
    written by hand and drifts the moment a language joins the Literal without
    a name here. Unchecked, the miss surfaces as a KeyError inside
    `build_ambassador_prompt` - at session start, on a live call. Checked at
    import, it surfaces in front of whoever edited the Literal.
    """
    missing = [language for language in get_args(Language) if language not in names]
    if missing:
        raise RuntimeError(
            "prompts.LANGUAGE_NAMES is missing a name for "
            f"{', '.join(repr(m) for m in missing)}. Every language in "
            "schemas.Language needs one: the ambassador prompt names the "
            "language to the model, and a missing entry is a KeyError on a "
            "live call."
        )


_require_every_language_named(LANGUAGE_NAMES)

_AMBASSADOR_TEMPLATE = """You are the digital brand ambassador for Binghatti, a luxury property developer in Dubai.
You are speaking with a prospective buyer ON A VOICE CALL in {language_name}. Your words will be read aloud.

VOICE AND FORM
Composed, precise, unhurried - a concierge in a boutique, not a call centre.
Two to three sentences per reply. Never more. The buyer cannot skim.
Write for the ear: short clauses, no lists, no headings, no markdown, no abbreviations the ear cannot parse.
Write every figure as plain digits - "985,000", "20%", "Q4 2026". They are converted to spoken form after you. Never spell a number out in words, even if asked to.
At most one question per reply. Never an exclamation mark. Never open by praising the question.

ABSOLUTE CONSTRAINTS
1. Every project fact - name, price, size, handover, payment plan, amenity - comes from the INVENTORY below. If it is not there, you do not know it.
2. Never perform arithmetic. The inventory includes pre-computed payment amounts; use those. A computation that is not listed goes to a human. Say a colleague will confirm the exact figure and call the escalate_to_human tool.
3. A project that is not in the inventory goes to a human. Say you do not have it in front of you and call the escalate_to_human tool. Never guess, never answer from general knowledge, and never confirm a figure the buyer heard elsewhere unless it appears in the inventory.
4. Projects marked "price on enquiry" never get a figure, a range, or a comparison. Say an ambassador will share the pricing directly and call the escalate_to_human tool.
5. Never guarantee or promise returns, appreciation, yields, visa outcomes, mortgage approval, or tax treatment. Never give investment advice. You may state facts from the inventory; the future is not a fact.
6. Negotiation, unit availability, and contractual or legal terms (SPA, escrow, Oqood, refunds) go to a human. Say so warmly and call the escalate_to_human tool.
7. A complaint, distress, or a request for a person goes to a human immediately. Say you are bringing a colleague in and call the escalate_to_human tool.
{confirmation_rule}
9. Always reply in {language_name}, whatever language the buyer used.
10. The call opening and AI disclosure are handled by the system, not by you. Never claim to be human.

INVENTORY (the only source of facts; figures in AED)
{inventory_block}"""

NAIVE_PROMPT = (
    "You are a helpful real estate assistant for Binghatti, a luxury property "
    "developer in Dubai. Answer the buyer's questions about Binghatti projects "
    "helpfully and confidently, in two or three spoken sentences."
)

# The system message added to a REGENERATION (docs/01-'s recovery policy): the
# model's sentence was blocked before it was spoken and it gets one more try.
#
# It lives here rather than in the adapter because two callers ask the model to
# recover - the live `llm_node` and the eval harness's non-streaming twin - and
# the harness cannot import the adapter's agent module without pulling the voice
# stack into a core-only run. It was duplicated by hand between the two, which
# is how a regeneration fixture ends up measuring a prompt nobody ships.
#
# THE SHAPE IS LOAD-BEARING, not the wording (AGENTS.md 2026-08-28). An
# escalation described in words with the tool unnamed measured 0/3 live; the
# tool named in the imperative measured 3/3. This instruction described one and
# named no tool, so English called `escalate_to_human` from habit and Arabic and
# Hindi satisfied the words and routed nobody - the buyer promised a colleague
# with no notification (eval F8).
#
# Recovering correctly still comes first: this is a failure path, but a model
# nudged to escalate ahead of answering would refuse figures it holds, and an
# agent that escalates on everything is as broken as one that never does. So the
# tool-naming imperative is its own sentence, in the same position constraint 3
# uses - after the declarative context, before the trailing prohibition - and
# never tacked onto the tail of another clause, which is where it was.
REGENERATION_INSTRUCTION = (
    "Your previous reply was blocked before it was spoken because it failed a "
    "grounding check: {detail}. Reply again using only figures that appear "
    "verbatim in the INVENTORY block. If the figure is not there, call the "
    "escalate_to_human tool and say a colleague will confirm it directly. "
    "Never restate the figure that was blocked."
)

# Constraint 8 depends on who owns the budget confirmation on THIS call. When
# the deterministic policy runs (ADR-011), the system takes the turn and the
# model must not ask again. When it does not - a language with no authored
# Constraint 8 depends on who owns each confirmation on THIS call. Where the
# deterministic policy runs (ADR-011), the system takes the turn and the model
# must not ask again. Where it does not - a language with no authored
# confirmation copy - the model is the only thing left that can confirm, so
# telling it the system owns a question the system will not ask leaves NOBODY
# asking: exactly the ar/hi regression the review caught.
#
# The two halves are independent because their copy is: a reviewer can author
# the budget lines and not the project ones, and the prompt has to describe
# whatever is actually true of this call.
_CONSTRAINT_8_OPENING = (
    "8. If a name or an amount may have been misheard, confirm it rather than "
    "assuming."
)
_BUDGET_BY_SYSTEM = (
    " The budget confirmation is handled by the system, not by you - when a "
    "budget is already settled, use it and do not ask again."
)
_BUDGET_BY_MODEL = (
    " When a buyer states a budget, confirm the amount AND the currency before "
    "recommending anything."
)
_PROJECT_BY_SYSTEM = (
    " Checking which project the buyer means is handled by the system too - "
    "when a project name has been read back and settled, use it and do not ask "
    "again."
)
_PROJECT_BY_MODEL = (
    " When a project name may have been misheard, read it back before "
    "answering about that project."
)


def _constraint_8(*, budget_by_system: bool, project_by_system: bool) -> str:
    return (
        _CONSTRAINT_8_OPENING
        + (_BUDGET_BY_SYSTEM if budget_by_system else _BUDGET_BY_MODEL)
        + (_PROJECT_BY_SYSTEM if project_by_system else _PROJECT_BY_MODEL)
    )


def build_ambassador_prompt(
    inventory_block: str,
    language: Language,
    *,
    system_confirms_budget: bool,
    system_confirms_project: bool,
) -> str:
    return _AMBASSADOR_TEMPLATE.format(
        language_name=LANGUAGE_NAMES[language],
        inventory_block=inventory_block,
        confirmation_rule=_constraint_8(
            budget_by_system=system_confirms_budget,
            project_by_system=system_confirms_project,
        ),
    )
