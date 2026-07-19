"""Deterministic policy rules and the typed decision contract.

Each rule is a pure function of the request. It returns a DENY
:class:`PolicyDecision` when violated and ``None`` when it passes. No rule
performs I/O or depends on wall-clock time, so evaluation is fully deterministic
and unit-testable. Financial or security facts are never inferred by an LLM.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from platform_control_plane.api.schemas import CreateAccountRequest

APPROVED_REGIONS = {"us-east-1", "us-west-2", "ca-central-1", "eu-central-1"}
PRODUCTION_ONLY_REGIONS: set[str] = set()  # placeholder for data-residency rules
MIN_MONTHLY_BUDGET = 50
NONPROD_BUDGET_CEILING = 10_000
RESERVED_NAME_PREFIXES = ("aws-", "amazon-", "internal-")


class PolicyEffect(enum.StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyDecision(BaseModel):
    """A single, traceable policy outcome."""

    policy_id: str
    decision: PolicyEffect
    reason: str
    remediation: str = ""


def _deny(policy_id: str, reason: str, remediation: str) -> PolicyDecision:
    return PolicyDecision(
        policy_id=policy_id,
        decision=PolicyEffect.DENY,
        reason=reason,
        remediation=remediation,
    )


def rule_account_name(request: CreateAccountRequest) -> PolicyDecision | None:
    """NAME-001: names must not use a reserved/vendor prefix."""
    lowered = request.account_name.lower()
    if lowered.startswith(RESERVED_NAME_PREFIXES):
        return _deny(
            "NAME-001",
            f"Account name '{request.account_name}' uses a reserved prefix.",
            "Choose a name that does not start with a vendor/reserved prefix.",
        )
    return None


def rule_region(request: CreateAccountRequest) -> PolicyDecision | None:
    """REGION-001: all regions must be on the approved list."""
    unapproved = [r for r in request.regions if r not in APPROVED_REGIONS]
    if unapproved:
        return _deny(
            "REGION-001",
            f"Regions not approved for use: {unapproved}.",
            f"Select from approved regions: {sorted(APPROVED_REGIONS)}.",
        )
    return None


def rule_budget_floor(request: CreateAccountRequest) -> PolicyDecision | None:
    """BUDGET-001: a minimum budget is required to cover baseline guardrails."""
    if request.monthly_budget < MIN_MONTHLY_BUDGET:
        return _deny(
            "BUDGET-001",
            f"Monthly budget ${request.monthly_budget} is below the "
            f"${MIN_MONTHLY_BUDGET} floor needed for baseline controls.",
            f"Set a monthly budget of at least ${MIN_MONTHLY_BUDGET}.",
        )
    return None


def rule_budget_ceiling(request: CreateAccountRequest) -> PolicyDecision | None:
    """BUDGET-002: large non-production budgets require manual review."""
    if request.monthly_budget > NONPROD_BUDGET_CEILING and request.environment != "production":
        return _deny(
            "BUDGET-002",
            f"Non-production budget ${request.monthly_budget} exceeds the "
            f"${NONPROD_BUDGET_CEILING} ceiling.",
            "Reduce the budget or request a production account with review.",
        )
    return None


def rule_data_classification(request: CreateAccountRequest) -> PolicyDecision | None:
    """DATA-001: sandbox accounts cannot host confidential/restricted data."""
    if (
        request.data_classification in ("confidential", "restricted")
        and request.environment == "sandbox"
    ):
        return _deny(
            "DATA-001",
            f"'{request.data_classification}' data is not allowed in a sandbox.",
            "Use a development, staging, or production environment.",
        )
    return None


# Ordered registry — evaluation order is stable and deterministic.
ALL_RULES = (
    rule_account_name,
    rule_region,
    rule_budget_floor,
    rule_budget_ceiling,
    rule_data_classification,
)
