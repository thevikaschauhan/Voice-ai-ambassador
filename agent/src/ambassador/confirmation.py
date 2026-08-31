"""Which of ADR-011's policies owns this turn (docs/04-).

Three deterministic policies want to read the buyer's words - the budget
confirmation, the project-name confirmation, and the failed-recognition count -
and at most one of them may speak. Deciding that used to be an ordering rule
inside the LiveKit adapter, and the eval harness reimplemented its own copy of
the same rule. Both are gone: the decision is pure, lives here, and is
unit-testable without a room, a socket or a vendor.

This module decides WHICH policy acts. It never composes copy, speaks, notifies
a human or logs; the caller does all of that, because those are the parts that
need a framework.

## Ordering is not the same thing as ownership

The first version ordered the policies - recognition, then budget, then project
- and that is necessary but not sufficient. It shipped two defects that an
independent review reproduced:

    Buyer:  Binghatti Skyrize
    Agent:  Just to be sure - did you mean Binghatti Skyrise?
    Buyer:  Yes, and my budget is 2 crore.
    Agent:  2 crore - is that in dirhams or in rupees?     <- the Yes is lost
    Buyer:  Dirhams.
    Agent:  Just to be sure - did you mean Binghatti Skyrise?
                                       <- and Dirhams counted as a failed
                                          project reply

The buyer answered both questions correctly and was handed to a human two
turns later. Precedence discarded an answer, and then a policy consumed an
answer addressed to a different question. That is the same class that blocked
the budget half twice, and no amount of reordering fixes it, because the two
failures want opposite orders.

## The rule

**A reply belongs to the question it answers.** Concretely, per turn:

1. **Recognition first.** A turn nobody could hear is not an answer to
   anything, so it is classified before any policy reads it as a reply. Three
   in a row hand over. Below three, whatever confirmation is owed is RE-ASKED
   and no attempt is consumed - a reply that was never heard is not a reply
   that was wrong.
2. **The owner reads it.** The owner is the policy whose question was asked
   most RECENTLY and is still open - the same question a person would take an
   answer to be about. If the reply actually answers it (`answers()`, a pure
   predicate), the owner acts. Any other open question is SUSPENDED: it is not
   read, so it cannot be answered by accident and cannot lose an attempt.
3. **Then fresh mentions.** Policies with no open question read the turn, in
   precedence order: the budget's twenty-times currency error is the most
   expensive thing on the call. This is why "Yes, and my budget is 2 crore"
   settles the name AND asks the currency - the answer is honoured first and
   the new mention still gets its question.
4. **A reply that answers nothing is a failed attempt** on the owner. Three of
   those hand over. Consent is never inferred from a reply nobody claimed.
5. **A suspended question is still owed** and is re-asked once its turn comes,
   having consumed nothing. The model never takes a turn while a confirmation
   is open, which is the property ADR-011 exists to hold.

## Handover quiesces everything

Any terminal handover - the recognition escalation, a budget or project
give-up, an unconvertible currency, or a caller that could not speak a
confirmation - closes every policy. Without that, the earlier defect was:

    Agent:  I am not hearing you clearly ... let me bring in one of our
            ambassadors.
    Buyer:  <empty>
    Agent:  2 crore - is that in dirhams or in rupees?

A human has been notified. An agent that then resumes a question from four
turns ago has not handed over, it has paused. Quiescing is deliberately by
CONSTRUCTION - every policy is abandoned - rather than by a guard at each read
site, because a guard is one site away from being forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .budget import BudgetPolicy, Decision
from .projects import ProjectDecision, ProjectNamePolicy
from .recognition import RecognitionDecision, RecognitionMonitor

Policy = Literal["budget", "project", "recognition"]

# Which policy reads a turn nobody has asked a question about, and in what
# order. The budget leads because a misread currency recommends a property up
# to twenty times off, which is the worst thing on the call.
_FRESH_ORDER: tuple[Policy, ...] = ("budget", "project")


@dataclass(frozen=True)
class Step:
    """One policy's reading of one turn.

    A turn produces a short sequence of these, in the order they happened, and
    at most the last one speaks. The caller walks them: every step is worth
    recording, and the speaking one is the turn.
    """

    policy: Policy
    budget: Decision | None = None
    project: ProjectDecision | None = None
    recognition: RecognitionDecision | None = None
    # True when this step re-speaks a question that was already asked, without
    # consuming an attempt: the turn was unheard, or the question was
    # suspended while another policy's answer came in.
    reask: bool = False

    @property
    def speaks(self) -> bool:
        decision = self.budget or self.project or self.recognition
        return decision is not None and decision.speaks

    @property
    def hands_over(self) -> bool:
        decision = self.budget or self.project or self.recognition
        return decision is not None and decision.hands_over

    @property
    def action(self) -> str:
        if self.recognition is not None:
            return self.recognition.action
        decision = self.budget or self.project
        return "none" if decision is None else decision.action


class ConfirmationCoordinator:
    """The three ADR-011 policies, and which of them owns each turn."""

    def __init__(
        self,
        *,
        budget: BudgetPolicy,
        project: ProjectNamePolicy,
        recognition: RecognitionMonitor,
        budget_runs: bool,
        project_runs: bool,
        recognition_runs: bool,
    ) -> None:
        self._budget = budget
        self._project = project
        self._recognition = recognition
        self._runs: dict[Policy, bool] = {
            "budget": budget_runs,
            "project": project_runs,
            "recognition": recognition_runs,
        }
        self._last_asked: Policy | None = None
        self._quiesced = False

    @property
    def quiesced(self) -> bool:
        return self._quiesced

    def quiesce(self) -> None:
        """Close every policy, because a human has the call now.

        Called on any terminal handover, and by the caller when it could not
        speak a confirmation at all. Abandoning each policy rather than setting
        a flag the read sites consult is the point: there is then no site that
        can forget to check.
        """
        self._quiesced = True
        self._last_asked = None
        self._budget.abandon()
        self._project.abandon()

    def observe(self, utterance: str) -> tuple[Step, ...]:
        """Every policy reading of this turn, in order. At most one speaks."""
        if self._quiesced:
            return ()

        steps: list[Step] = []
        unheard = False
        if self._runs["recognition"]:
            heard = self._recognition.observe(utterance)
            steps.append(Step("recognition", recognition=heard))
            unheard = heard.failed
            if heard.speaks:
                return self._closing(steps)

        if unheard:
            # Not an answer to anything - but a confirmation that was owed is
            # still owed, and handing the turn back to the model while one is
            # open is the property this whole feature exists to hold.
            return self._with_reask(steps)

        owner = self._owner()
        if owner is not None and self._answers(owner, utterance):
            steps.append(self._read(owner, utterance))
            if steps[-1].speaks:
                return self._closing(steps)
            # Settled silently; the turn may still carry a fresh mention.

        for name in _FRESH_ORDER:
            if any(step.policy == name for step in steps):
                continue
            if not self._runs[name] or self._pending(name) is not None:
                # A suspended question does not read the turn: that is exactly
                # how a reply meant for another question became a failed
                # attempt.
                continue
            steps.append(self._read(name, utterance))
            if steps[-1].speaks:
                return self._closing(steps)

        if owner is not None and not any(step.policy == owner for step in steps):
            # Nobody claimed the reply, so it answered nothing. Consent is
            # never inferred from that; it is a failed attempt, and three of
            # them hand the buyer to a person.
            steps.append(self._read(owner, utterance))
            if steps[-1].speaks:
                return self._closing(steps)

        return self._with_reask(steps)

    # -- internals -----------------------------------------------------------

    def _closing(self, steps: list[Step]) -> tuple[Step, ...]:
        """Record who spoke, and quiesce if the turn handed the buyer over."""
        last = steps[-1]
        if last.hands_over:
            self.quiesce()
        elif last.speaks and self._pending(last.policy) is not None:
            self._last_asked = last.policy
        return tuple(steps)

    def _with_reask(self, steps: list[Step]) -> tuple[Step, ...]:
        """Re-speak a question that is still owed, consuming nothing."""
        owner = self._owner()
        if owner is None:
            return tuple(steps)
        pending = self._pending(owner)
        if pending is None:  # pragma: no cover - _owner only names open ones
            return tuple(steps)
        self._last_asked = owner
        if owner == "budget":
            steps.append(Step("budget", budget=pending, reask=True))
        else:
            steps.append(Step("project", project=pending, reask=True))
        return tuple(steps)

    def _owner(self) -> Policy | None:
        """The policy whose question was asked most recently and is still open.

        The most recent question is the one a person would take an answer to be
        about, which is the whole of the ownership rule.
        """
        open_now = [
            name for name in _FRESH_ORDER if self._pending(name) is not None
        ]
        if not open_now:
            return None
        if self._last_asked in open_now:
            return self._last_asked
        return open_now[0]

    def _pending(self, name: Policy):
        if not self._runs.get(name):
            return None
        if name == "budget":
            return self._budget.pending
        if name == "project":
            return self._project.pending
        return None

    def _answers(self, name: Policy, utterance: str) -> bool:
        if name == "budget":
            return self._budget.answers(utterance)
        return self._project.answers(utterance)

    def _read(self, name: Policy, utterance: str) -> Step:
        if name == "budget":
            return Step("budget", budget=self._budget.observe(utterance))
        return Step("project", project=self._project.observe(utterance))
