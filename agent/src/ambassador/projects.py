"""Project-name recognition and the confirmation policy for it (ADR-011).

The second of ADR-011's four triggers: *project names are confirmed when the
fuzzy match against inventory is marginal*. The budget half lives in
`budget.py` and this is deliberately the same shape - a pure state machine
that the adapter speaks for, taking the turn away from the model so the
question cannot be skipped, reworded or answered on the buyer's behalf.

## The failure this exists to prevent

Every recogniser measured in ADR-015 mangled the client's own name:
"Binghatti" came back as "Bint Jbeil", "Binghati", "binghati", and OpenRouter's
transcription endpoint ignored the biasing parameter, so nothing fixes
recognition of it on the input side. Two of the four projects in inventory are
`Binghatti Skyrise` and `Binghatti Aquarise` - near-homophones that differ in
one token and share the "-rise" ending - at different prices. A transcript
reading "Binghatti Sky Rise" when the buyer said Aquarise, answered
confidently, quotes the wrong project's price. That is the same class of error
as the budget policy's twenty-times mistake, arriving by a different door.

## Marginal, defined

Matching is deterministic and derived from inventory, never from a
hand-authored alias table: an alias list is a second source of project facts,
and invariant 1 allows exactly one.

For each project the index holds three kinds of key - the full name, the
tokens unique to that project, and each unique token on its own - so a buyer
who says "Skyrise" and a buyer who says "Binghatti Skyrise" are both heard.
Each key is scored against the utterance by aligning its tokens against every
window of the buyer's tokens (`difflib.SequenceMatcher` per token pair, from
the standard library - no new dependency). Two numbers come out: `similarity`,
the mean per-token ratio, which decides how sure we are, and `coverage`, the
sum, which decides how much of the utterance the key explains.

Then the bands:

- below `_FLOOR` - no project name was said. Silence; the model answers, and
  prompt constraint 3 escalates a project that is not in inventory.
- at or above `_CONFIDENT` with a clear `_MARGIN` over the runner-up - one
  project, unambiguously. Silence again: a read-back nobody needs is the
  fastest way to have the policy switched off.
- anything between - MARGINAL. The policy takes the turn and asks.

## Decoys: what stops the silly question

Scoring project keys alone makes "Jumeirah Village Circle" (an area, and the
area `Binghatti Circle` sits in) match the project, and "tell me about
Binghatti" match whichever project name is nearest to the bare brand word.
Both are wrong and both are visible in a demo.

So the index also holds DECOY keys: every area name in inventory, its
individual tokens, and every token shared by more than one project name
(`binghatti` itself). A decoy that is a credible match in its own right
(similarity at or above the floor) and explains at least as much of the
utterance as the best project key means no project was named. Ties go to the
decoy, because "Circle" on its own genuinely is the area as much as the
project.

The comparison is on COVERAGE, not similarity, and that is the load-bearing
detail: "Binghatti Skyrize" contains `binghatti` exactly, so the decoy scores
1.0 similarity and would suppress the match this whole module exists for - but
it explains one token where `binghatti skyrise` explains two.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from .budget import CurrencyVocabulary
from .figures import normalise_digits
from .schemas import Project

# Tokens shorter than this carry no evidence and drag a mean down: "by" in
# "Bugatti Residences by Binghatti" made a perfectly recognised name read as
# marginal.
_MIN_TOKEN = 3

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

# Calibrated against a corpus of realistic buyer utterances (both the mangled
# project names the recognisers actually returned in ADR-015 and 46 ordinary
# non-project questions), and pinned by tests. The floor is where it is
# because "budget" scores 0.615 against "bugatti": a lower floor turns the
# budget policy's own opening line into a project question.
_FLOOR = 0.68
_CONFIDENT = 0.95
_MARGIN = 0.12

Band = Literal["confident", "marginal"]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        t for t in _TOKEN.findall(normalise_digits(text).lower()) if len(t) >= _MIN_TOKEN
    )


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _coverage(said: tuple[str, ...], key: tuple[str, ...]) -> float:
    """The best contiguous alignment of `key` against `said`, summed over the
    key's tokens.

    Contiguous on purpose: a project name is spoken as one phrase, and letting
    the tokens match anywhere would score "Skyrise ... Binghatti is the
    developer" as a full-name match.
    """
    if not said:
        return 0.0
    best = 0.0
    for start in range(len(said)):
        total = 0.0
        for offset, expected in enumerate(key):
            here = start + offset
            if here >= len(said):
                break
            total += _ratio(said[here], expected)
        best = max(best, total)
    return best


@dataclass(frozen=True)
class NameIndex:
    """Everything derived from inventory that name matching needs.

    Built once. `names` is the only place a spoken project name comes from -
    the confirmation's slot is bound to these values, which is what keeps
    invariant 1 true on this path.
    """

    keys: tuple[tuple[str, tuple[str, ...]], ...]
    names: dict[str, str]
    decoys: tuple[tuple[str, ...], ...]


def build_name_index(projects: list[Project]) -> NameIndex:
    names = {p.id: p.name for p in projects}

    appearances: Counter[str] = Counter()
    for name in names.values():
        appearances.update(set(_tokens(name)))
    shared = {token for token, count in appearances.items() if count > 1}

    keys: list[tuple[str, tuple[str, ...]]] = []
    for project_id, name in names.items():
        full = _tokens(name)
        if not full:
            continue
        candidates = {full}
        distinctive = tuple(t for t in full if t not in shared)
        if distinctive:
            candidates.add(distinctive)
            candidates.update((token,) for token in distinctive)
        keys.extend((project_id, key) for key in sorted(candidates))

    decoys: set[tuple[str, ...]] = {(token,) for token in shared}
    for project in projects:
        area = _tokens(project.area)
        if area:
            decoys.add(area)
            decoys.update((token,) for token in area)

    return NameIndex(keys=tuple(keys), names=names, decoys=tuple(sorted(decoys)))


@dataclass(frozen=True)
class NameMatch:
    """One project the buyer probably named, and how sure we are."""

    project_id: str
    name: str
    band: Band
    similarity: float
    runner_up: float


def match_project_name(
    utterance: str, index: NameIndex, *, exclude: frozenset[str] = frozenset()
) -> NameMatch | None:
    """The project named in this utterance, or None.

    `exclude` drops projects the policy has already settled or the buyer has
    already rejected, so a confirmed name is not read back twice and a
    rejected one is never offered again.
    """
    said = _tokens(utterance)
    if not said:
        return None

    best: dict[str, tuple[float, float]] = {}
    for project_id, key in index.keys:
        if project_id in exclude:
            continue
        coverage = _coverage(said, key)
        similarity = coverage / len(key)
        held = best.get(project_id)
        # Similarity decides, then coverage. The tie-break is not cosmetic:
        # "Binghatti Aquarise" matches both the full name and the bare
        # distinctive token at similarity 1.0, and keeping the SHORTER one
        # left the match explaining a single token - which the `binghatti`
        # decoy then tied with and suppressed. Prefer the key that explains
        # more of what the buyer said.
        if held is None or (similarity, coverage) > held:
            best[project_id] = (similarity, coverage)
    if not best:
        return None

    ranked = sorted(
        ((similarity, coverage, pid) for pid, (similarity, coverage) in best.items()),
        reverse=True,
    )
    similarity, coverage, project_id = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if similarity < _FLOOR:
        return None

    # A decoy only competes once it is a credible match itself; otherwise a
    # three-token area name accumulates coverage out of unrelated words and
    # suppresses everything.
    for decoy in index.decoys:
        decoy_coverage = _coverage(said, decoy)
        if decoy_coverage / len(decoy) >= _FLOOR and decoy_coverage >= coverage:
            return None

    band: Band = (
        "confident"
        if similarity >= _CONFIDENT and (similarity - runner_up) > _MARGIN
        else "marginal"
    )
    return NameMatch(
        project_id=project_id,
        name=index.names[project_id],
        band=band,
        similarity=similarity,
        runner_up=runner_up,
    )


# --- reading a reply to the confirmation -------------------------------------


@dataclass(frozen=True)
class AgreementWords:
    """The yes/no words, per language.

    They come from `data/currencies.yaml` because that is where the budget
    policy's native reviewer already authors them, and they are not about
    currency: "no", "that's wrong", "yes", "correct" answer any read-back.
    A second copy in a second file is a second architecture for the same job,
    and the one that goes stale is the one nobody is looking at.
    """

    affirmations: dict[str, tuple[str, ...]]
    contradictions: dict[str, tuple[str, ...]]
    negators: dict[str, tuple[str, ...]]

    def languages_covered(self) -> frozenset[str]:
        """Languages whose buyer can say yes or no and be heard."""
        return frozenset(
            language
            for language in set(self.affirmations) | set(self.contradictions)
            if self.affirmations.get(language) and self.contradictions.get(language)
        )


def agreement_words(vocabulary: CurrencyVocabulary) -> AgreementWords:
    return AgreementWords(
        affirmations=vocabulary.affirmations,
        contradictions=vocabulary.contradictions,
        negators=vocabulary.negators,
    )


@dataclass(frozen=True)
class Agreement:
    """`contradicted` is read before `agreed` and wins over it.

    That precedence is not a style choice: "no, that's right" and "yes, not
    that one" both carry an agreement word, and reading the agreement first
    recorded a rejection as consent - the defect that blocked the budget half
    twice, once for each phrasing.
    """

    agreed: bool
    contradicted: bool


def _token_pattern(token: str) -> str:
    return rf"(?<!\w){re.escape(token)}(?!\w)" if token.isalnum() else re.escape(token)


def _says_any(lowered: str, words: tuple[str, ...]) -> bool:
    return any(word and re.search(_token_pattern(word), lowered) for word in words)


def read_agreement(
    utterance: str, words: AgreementWords, language: str
) -> Agreement:
    """Did this reply accept the project we named, reject it, or neither?

    A negator counts as a contradiction here, unlike in the budget policy
    where it may be denying a currency instead. There is no currency in a
    project read-back, so "not that one" and "I don't think so" are rejections
    with nothing else to be.
    """
    lowered = normalise_digits(utterance).lower()
    contradicted = _says_any(
        lowered, words.contradictions.get(language, ())
    ) or _says_any(lowered, words.negators.get(language, ()))
    agreed = not contradicted and _says_any(
        lowered, words.affirmations.get(language, ())
    )
    return Agreement(agreed=agreed, contradicted=contradicted)


# --- the policy state machine ------------------------------------------------

# Each speaking action is also the key its copy lives under in
# data/confirmations.yaml, exactly as the budget policy's are. One name for
# one thing: a decision whose action does not name its own copy is a lookup
# waiting to go wrong.
Action = Literal["none", "confirm_project", "ask_project", "project_give_up"]

_Question = Literal["confirm_project", "ask_project"]

# Same three as the budget policy, for the same reason (docs/04-), and counted
# separately: a buyer who cannot settle a project name has not failed to
# settle a budget.
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ProjectDecision:
    action: Action
    project_id: str | None = None
    name: str | None = None
    band: Band | None = None
    similarity: float | None = None

    @property
    def speaks(self) -> bool:
        return self.action != "none"

    @property
    def settled(self) -> bool:
        """A project the policy is now sure of. True on a confident match with
        no question asked as well as on an accepted read-back."""
        return self.action == "none" and self.project_id is not None

    @property
    def hands_over(self) -> bool:
        """The caller must actually notify a human, not merely say so - the
        anti-pattern `escalate_to_human`'s own docstring names."""
        return self.action == "project_give_up"


class ProjectNamePolicy:
    """One buyer's project names, across a call.

    Unlike the budget policy this does not settle permanently: a call moves
    from one project to another, and each marginal mention deserves its own
    question. What is permanent is `give_up` - once a human has been brought
    in, the policy stops speaking.

    Two sets carry the memory. A project the buyer has confirmed is never read
    back again; a project the buyer has rejected is never offered again, which
    is what stops "did you mean Skyrise?" / "no" / "did you mean Skyrise?".
    """

    def __init__(
        self, index: NameIndex, words: AgreementWords, language: str
    ) -> None:
        self._index = index
        self._words = words
        self._language = language
        self._offered: NameMatch | None = None
        self._asked: _Question | None = None
        self._attempts = 0
        self._confirmed: set[str] = set()
        self._rejected: set[str] = set()
        self._handed_over = False

    @property
    def handed_over(self) -> bool:
        return self._handed_over

    @property
    def confirmed(self) -> frozenset[str]:
        return frozenset(self._confirmed)

    @property
    def pending(self) -> ProjectDecision | None:
        """The question this policy is waiting on an answer to, if any.

        Read-only and non-mutating: the caller re-speaks it on a turn nobody
        could hear, and a turn nobody could hear must not consume an attempt.
        """
        if self._asked is None:
            return None
        return self._decision(self._asked)

    def observe(self, utterance: str) -> ProjectDecision:
        if self._handed_over:
            return ProjectDecision("none")
        if self._asked is not None:
            return self._answer(utterance)
        return self._first_mention(utterance)

    def abandon(self) -> None:
        """Give up without an answer: the caller could not speak the question
        (blocked copy, a name the guardrail refused) and has routed the buyer
        to a human instead.

        This is what makes that path fail CLOSED. Leaving the question open
        and handing the turn back to the model is the fail-open defect the
        budget half shipped and had to have removed.
        """
        self._handed_over = True
        self._asked = None

    # -- internals -----------------------------------------------------------

    def _match(self, utterance: str) -> NameMatch | None:
        return match_project_name(
            utterance,
            self._index,
            exclude=frozenset(self._confirmed | self._rejected),
        )

    def _first_mention(self, said: str) -> ProjectDecision:
        match = self._match(said)
        if match is None:
            return ProjectDecision("none")
        return self._open(match)

    def _open(self, match: NameMatch) -> ProjectDecision:
        """Settle a confident match silently, or ask about a marginal one."""
        if match.band == "confident":
            return self._settle(match)
        self._offered = match
        self._asked = "confirm_project"
        return self._decision("confirm_project")

    def _answer(self, said: str) -> ProjectDecision:
        assert self._asked is not None

        if self._asked == "ask_project":
            # "Which project was that?" - anything in the reply that names one
            # is the answer, and starts a fresh confirmation.
            fresh = self._match(said)
            if fresh is None:
                return self._failed_attempt()
            self._attempts = 0
            return self._open(fresh)

        assert self._offered is not None
        fresh = self._match(said)
        if fresh is not None and fresh.project_id != self._offered.project_id:
            # A different project named in the reply replaces the offer, the
            # way a restated budget replaces a stale mention. Settling the
            # project we guessed against a reply that named another one is the
            # same shipped defect wearing different clothes.
            self._attempts = 0
            return self._open(fresh)

        reading = read_agreement(said, self._words, self._language)
        if reading.contradicted:
            self._rejected.add(self._offered.project_id)
            self._offered = None
            return self._failed_attempt(reopen="ask_project")
        if reading.agreed:
            return self._settle(self._offered)
        # Consent is never inferred. "Can you repeat that?" carries no signal,
        # and three of those hand the buyer to a person.
        return self._failed_attempt()

    def _failed_attempt(self, reopen: _Question | None = None) -> ProjectDecision:
        self._attempts += 1
        if self._attempts >= _MAX_ATTEMPTS:
            self._handed_over = True
            self._asked = None
            return ProjectDecision("project_give_up")
        if reopen is not None:
            self._asked = reopen
        assert self._asked is not None
        return self._decision(self._asked)

    def _settle(self, match: NameMatch) -> ProjectDecision:
        self._confirmed.add(match.project_id)
        self._offered = None
        self._asked = None
        self._attempts = 0
        return ProjectDecision(
            "none",
            project_id=match.project_id,
            name=match.name,
            band=match.band,
            similarity=match.similarity,
        )

    def _decision(self, action: _Question) -> ProjectDecision:
        offered = self._offered
        if offered is None:
            return ProjectDecision(action)
        return ProjectDecision(
            action,
            project_id=offered.project_id,
            name=offered.name,
            band=offered.band,
            similarity=offered.similarity,
        )
