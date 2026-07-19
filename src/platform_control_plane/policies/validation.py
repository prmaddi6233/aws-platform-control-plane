"""Backward-compatible validation wrapper around the policy engine.

Prefer :class:`platform_control_plane.policies.engine.PolicyEngine` for new
code — it returns a structured :class:`PolicyEvaluation`. This function keeps a
raise-on-violation entry point for callers that want fail-fast semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from platform_control_plane.exceptions import PolicyViolationError
from platform_control_plane.policies.engine import PolicyEngine

if TYPE_CHECKING:
    from platform_control_plane.api.schemas import CreateAccountRequest

_ENGINE = PolicyEngine()


def validate_account_request(request: CreateAccountRequest) -> None:
    """Evaluate policies and raise :class:`PolicyViolationError` on any denial."""
    evaluation = _ENGINE.evaluate(request)
    if not evaluation.allowed:
        raise PolicyViolationError(
            "Account request failed platform policy validation.",
            violations=[d.model_dump() for d in evaluation.denials],
        )
