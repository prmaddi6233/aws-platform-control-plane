"""Policy engine — evaluates a request against all rules deterministically."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from platform_control_plane.policies.rules import ALL_RULES, PolicyDecision
from platform_control_plane.telemetry.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from platform_control_plane.api.schemas import CreateAccountRequest

    Rule = Callable[[CreateAccountRequest], PolicyDecision | None]

logger = get_logger(__name__)


class PolicyEvaluation(BaseModel):
    """The aggregate result of evaluating every rule against a request."""

    allowed: bool
    denials: list[PolicyDecision]

    @property
    def summary(self) -> str:
        if self.allowed:
            return "ALLOW"
        ids = ", ".join(d.policy_id for d in self.denials)
        return f"DENY ({ids})"


class PolicyEngine:
    """Runs the ordered rule set and aggregates the decision."""

    def __init__(self, rules: tuple[Rule, ...] = ALL_RULES) -> None:
        self._rules = rules

    def evaluate(self, request: CreateAccountRequest) -> PolicyEvaluation:
        denials = [d for rule in self._rules if (d := rule(request)) is not None]
        evaluation = PolicyEvaluation(allowed=not denials, denials=denials)
        logger.info(
            "policy_evaluated",
            account_name=request.account_name,
            result=evaluation.summary,
            denial_count=len(denials),
        )
        return evaluation
