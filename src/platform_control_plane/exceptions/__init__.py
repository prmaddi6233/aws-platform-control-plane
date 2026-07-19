"""Explicit exception hierarchy for the platform control plane.

Design decision: every exception carries structured context so that upstream
handlers (Lambda, Step Functions catch blocks, CLI) can produce actionable
error responses without parsing message strings.

Exception tree:
    PlatformError
    ├── ValidationError          — request schema or policy violation
    ├── StateTransitionError     — illegal state machine transition
    ├── DuplicateRequestError    — idempotency key collision
    ├── AccountNotFoundError     — lookup miss in the repository
    ├── PolicyViolationError     — policy engine rejection
    ├── WorkflowStepError        — a single workflow step failed
    │   └── RetryableStepError   — transient failure, safe to retry
    └── ConcurrencyError         — optimistic lock / conditional write failure
"""

from __future__ import annotations

from typing import Any

from platform_control_plane.domain.failures import FailureClass


class PlatformError(Exception):
    """Base exception for all platform control plane errors.

    Every subclass carries a ``code`` for machine consumption, a ``details`` dict
    for structured logging, and a ``failure_class`` linking it to the shared
    operational taxonomy (retryability, correctability, alert severity).
    """

    code: str = "PLATFORM_ERROR"
    failure_class: FailureClass = FailureClass.MANUAL_INTERVENTION_REQUIRED

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = details or {}


class ValidationError(PlatformError):
    """Request failed schema or business-rule validation."""

    code = "VALIDATION_ERROR"
    failure_class = FailureClass.VALIDATION_ERROR


class StateTransitionError(PlatformError):
    """Attempted an illegal state transition on an account request."""

    code = "STATE_TRANSITION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        current_state: str,
        attempted_state: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "current_state": current_state,
            "attempted_state": attempted_state,
            **(details or {}),
        }
        super().__init__(message, details=merged)
        self.current_state = current_state
        self.attempted_state = attempted_state


class AuthorizationError(PlatformError):
    """The actor is not permitted to perform the action (e.g. self-approval)."""

    code = "AUTHORIZATION_ERROR"
    failure_class = FailureClass.AUTHORIZATION_ERROR


class DuplicateRequestError(PlatformError):
    """A request with the same idempotency key already exists."""

    code = "DUPLICATE_REQUEST"
    failure_class = FailureClass.IDEMPOTENCY_CONFLICT

    def __init__(
        self,
        message: str,
        *,
        idempotency_key: str,
        existing_request_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "idempotency_key": idempotency_key,
            "existing_request_id": existing_request_id,
            **(details or {}),
        }
        super().__init__(message, details=merged)
        self.idempotency_key = idempotency_key
        self.existing_request_id = existing_request_id


class AccountNotFoundError(PlatformError):
    """No account request found for the given identifier."""

    code = "ACCOUNT_NOT_FOUND"
    failure_class = FailureClass.VALIDATION_ERROR


class PolicyViolationError(PlatformError):
    """One or more policies rejected the request."""

    code = "POLICY_VIOLATION"
    failure_class = FailureClass.POLICY_DENIED

    def __init__(
        self,
        message: str,
        *,
        violations: list[dict[str, Any]],
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {"violations": violations, **(details or {})}
        super().__init__(message, details=merged)
        self.violations = violations


class WorkflowStepError(PlatformError):
    """A workflow step failed during execution."""

    code = "WORKFLOW_STEP_ERROR"
    failure_class = FailureClass.PERMANENT_DEPENDENCY_FAILURE

    def __init__(
        self,
        message: str,
        *,
        step_name: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {"step_name": step_name, **(details or {})}
        super().__init__(message, details=merged)
        self.step_name = step_name


class RetryableStepError(WorkflowStepError):
    """A transient failure in a workflow step — safe to retry with backoff."""

    code = "RETRYABLE_STEP_ERROR"
    failure_class = FailureClass.RETRYABLE_DEPENDENCY_FAILURE

    def __init__(
        self,
        message: str,
        *,
        step_name: str,
        retry_after_seconds: int = 30,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {"retry_after_seconds": retry_after_seconds, **(details or {})}
        super().__init__(message, step_name=step_name, details=merged)
        self.retry_after_seconds = retry_after_seconds


class ConcurrencyError(PlatformError):
    """Optimistic locking failure — another writer modified the record."""

    code = "CONCURRENCY_ERROR"
    failure_class = FailureClass.CONCURRENCY_CONFLICT


class IdempotencyConflictError(PlatformError):
    """An idempotency key was reused with a materially different payload.

    Distinct from :class:`DuplicateRequestError`: a duplicate with an *identical*
    payload is a safe replay (the original result is returned), whereas a reused
    key with a *different* payload is a client error that must be surfaced.
    """

    code = "IDEMPOTENCY_CONFLICT"
    failure_class = FailureClass.IDEMPOTENCY_CONFLICT

    def __init__(
        self,
        message: str,
        *,
        idempotency_key: str,
        existing_request_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "idempotency_key": idempotency_key,
            "existing_request_id": existing_request_id,
            **(details or {}),
        }
        super().__init__(message, details=merged)
        self.idempotency_key = idempotency_key
        self.existing_request_id = existing_request_id


class ApprovalError(PlatformError):
    """An approval action is invalid — e.g. the approval window has expired."""

    code = "APPROVAL_ERROR"
    failure_class = FailureClass.APPROVAL_TIMEOUT


class ActiveDecommissionExistsError(PlatformError):
    """A non-terminal decommissioning request already exists for the account."""

    code = "ACTIVE_DECOMMISSION_EXISTS"
    failure_class = FailureClass.IDEMPOTENCY_CONFLICT

    def __init__(
        self,
        message: str,
        *,
        aws_account_id: str,
        existing_decommission_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "aws_account_id": aws_account_id,
            "existing_decommission_id": existing_decommission_id,
            **(details or {}),
        }
        super().__init__(message, details=merged)
        self.aws_account_id = aws_account_id
        self.existing_decommission_id = existing_decommission_id


class AuditIntegrityError(PlatformError):
    """An audit append violated append-only/hash-chain integrity."""

    code = "AUDIT_INTEGRITY_ERROR"


class StoreError(PlatformError):
    """Base for local persistence-store errors."""

    code = "STORE_ERROR"


class StoreCorruptError(StoreError):
    """The local store file could not be parsed and must not be silently discarded."""

    code = "STORE_CORRUPT"


class UnsupportedStoreVersionError(StoreError):
    """The local store file has a version this build does not understand."""

    code = "UNSUPPORTED_STORE_VERSION"
