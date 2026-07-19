"""Explicit operational failure taxonomy — the single authoritative registry.

Every failure the control plane can produce is classified once, here, with the
operational metadata downstream consumers need: retryability, who can correct
it, the recommended next action, alert severity, and the CLI/simulator exit
code. Domain exceptions, the provisioning and decommissioning services, the CLI,
the workflow simulator, audit events, Step Functions catch paths, and the docs
all refer to this registry so classification never drifts between layers.

CLI / simulator exit-code convention:
    0 = completed with the expected successful result
    2 = expected business or policy block
    3 = validation or usage error
    4 = unexpected / unrecoverable platform failure
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class FailureClass(enum.StrEnum):
    """The canonical set of failure classifications."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    POLICY_DENIED = "POLICY_DENIED"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
    RETRYABLE_DEPENDENCY_FAILURE = "RETRYABLE_DEPENDENCY_FAILURE"
    PERMANENT_DEPENDENCY_FAILURE = "PERMANENT_DEPENDENCY_FAILURE"
    COMMITMENT_BLOCK = "COMMITMENT_BLOCK"
    RESIDUAL_SPEND_BLOCK = "RESIDUAL_SPEND_BLOCK"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


class Correctability(enum.StrEnum):
    USER = "USER"  # the requester can fix and resubmit
    PLATFORM = "PLATFORM"  # a platform engineer must intervene
    OPERATOR = "OPERATOR"  # an operator/approver action resolves it


class AlertSeverity(enum.StrEnum):
    NONE = "NONE"
    INFO = "INFO"
    WARNING = "WARNING"
    PAGE = "PAGE"


@dataclass(frozen=True)
class FailureProfile:
    """Operational metadata for one failure class."""

    failure_class: FailureClass
    retryable: bool
    correctable_by: Correctability
    recommended_action: str
    alert_severity: AlertSeverity
    cli_exit_code: int


def _p(
    fc: FailureClass,
    retryable: bool,
    correctable: Correctability,
    action: str,
    severity: AlertSeverity,
    exit_code: int,
) -> FailureProfile:
    return FailureProfile(fc, retryable, correctable, action, severity, exit_code)


_REGISTRY: dict[FailureClass, FailureProfile] = {
    FailureClass.VALIDATION_ERROR: _p(
        FailureClass.VALIDATION_ERROR,
        False,
        Correctability.USER,
        "Fix the request payload and resubmit.",
        AlertSeverity.NONE,
        3,
    ),
    FailureClass.POLICY_DENIED: _p(
        FailureClass.POLICY_DENIED,
        False,
        Correctability.USER,
        "Adjust the request to satisfy the failed policy, then resubmit.",
        AlertSeverity.NONE,
        2,
    ),
    FailureClass.AUTHORIZATION_ERROR: _p(
        FailureClass.AUTHORIZATION_ERROR,
        False,
        Correctability.OPERATOR,
        "Obtain authorization from a distinct approver (separation of duties).",
        AlertSeverity.INFO,
        2,
    ),
    FailureClass.IDEMPOTENCY_CONFLICT: _p(
        FailureClass.IDEMPOTENCY_CONFLICT,
        False,
        Correctability.USER,
        "Use a new idempotency key or resend the identical original payload.",
        AlertSeverity.NONE,
        2,
    ),
    FailureClass.CONCURRENCY_CONFLICT: _p(
        FailureClass.CONCURRENCY_CONFLICT,
        True,
        Correctability.PLATFORM,
        "Re-read the current state and retry the transition.",
        AlertSeverity.INFO,
        4,
    ),
    FailureClass.APPROVAL_TIMEOUT: _p(
        FailureClass.APPROVAL_TIMEOUT,
        False,
        Correctability.USER,
        "Resubmit the request; the approval window elapsed.",
        AlertSeverity.INFO,
        2,
    ),
    FailureClass.RETRYABLE_DEPENDENCY_FAILURE: _p(
        FailureClass.RETRYABLE_DEPENDENCY_FAILURE,
        True,
        Correctability.PLATFORM,
        "Allow bounded retries with backoff; investigate if retries exhaust.",
        AlertSeverity.WARNING,
        4,
    ),
    FailureClass.PERMANENT_DEPENDENCY_FAILURE: _p(
        FailureClass.PERMANENT_DEPENDENCY_FAILURE,
        False,
        Correctability.PLATFORM,
        "Remediate the root cause (e.g. SCP/quota), then re-drive the workflow.",
        AlertSeverity.PAGE,
        4,
    ),
    FailureClass.COMMITMENT_BLOCK: _p(
        FailureClass.COMMITMENT_BLOCK,
        False,
        Correctability.USER,
        "Reassign/modify the commitment or explicitly accept the remaining cost.",
        AlertSeverity.WARNING,
        2,
    ),
    FailureClass.RESIDUAL_SPEND_BLOCK: _p(
        FailureClass.RESIDUAL_SPEND_BLOCK,
        False,
        Correctability.USER,
        "Disable remaining workloads until spend falls below threshold.",
        AlertSeverity.WARNING,
        2,
    ),
    FailureClass.WORKFLOW_TIMEOUT: _p(
        FailureClass.WORKFLOW_TIMEOUT,
        False,
        Correctability.PLATFORM,
        "Inspect the stalled execution and route to manual intervention.",
        AlertSeverity.PAGE,
        4,
    ),
    FailureClass.MANUAL_INTERVENTION_REQUIRED: _p(
        FailureClass.MANUAL_INTERVENTION_REQUIRED,
        False,
        Correctability.PLATFORM,
        "A platform engineer must resolve and re-drive per the runbook.",
        AlertSeverity.PAGE,
        4,
    ),
}


def profile_for(failure_class: FailureClass) -> FailureProfile:
    """Return the operational profile for a failure class."""
    return _REGISTRY[failure_class]


def all_profiles() -> list[FailureProfile]:
    return list(_REGISTRY.values())
