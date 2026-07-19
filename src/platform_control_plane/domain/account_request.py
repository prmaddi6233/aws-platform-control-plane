"""Domain models and state machine for AWS Account Requests."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from platform_control_plane.domain import (
    AccountProvisioningCompleted,
    AccountProvisioningFailed,
    AccountProvisioningStarted,
    AccountRequestApproved,
    AccountRequestSubmitted,
    DomainEvent,
)
from platform_control_plane.exceptions import StateTransitionError


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _generate_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


class RequestState(enum.StrEnum):
    """The lifecycle states of an account request.

    ``ACTION_REQUIRED`` is distinct from ``FAILED``: it marks a request whose
    account was created but whose baseline could not be fully applied, so the
    account is quarantined pending human remediation and workflow re-drive.
    ``FAILED`` is reserved for hard failures with no created account.
    """

    SUBMITTED = "SUBMITTED"
    POLICY_REJECTED = "POLICY_REJECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PROVISIONING = "PROVISIONING"
    COMPLETED = "COMPLETED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AccountRequest(BaseModel):
    """The core domain entity representing a request for a new AWS account.

    This entity encapsulates the state machine for account vending. State
    transitions are explicit methods that raise StateTransitionError if an
    illegal transition is attempted.
    """

    request_id: str = Field(default_factory=_generate_id)
    idempotency_key: str
    account_name: str
    owner: str
    environment: str
    data_classification: str
    monthly_budget: int
    regions: list[str]

    # Fingerprint of the semantic payload (excludes the idempotency key itself).
    # Used to distinguish a safe replay from an idempotency-key conflict.
    payload_fingerprint: str = ""

    state: RequestState = RequestState.SUBMITTED
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # Output fields populated during provisioning
    aws_account_id: str | None = None
    provisioning_error: str | None = None

    # Track uncommitted domain events (Outbox pattern)
    _events: list[DomainEvent] = []

    model_config = {"extra": "ignore"}

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._events = []
        if self.state == RequestState.SUBMITTED and not self.aws_account_id:
            # Emit initial event if this is a fresh request
            self._record_event(
                AccountRequestSubmitted(
                    request_id=self.request_id,
                    account_name=self.account_name,
                    owner=self.owner,
                    environment=self.environment,
                )
            )

    def _record_event(self, event: DomainEvent) -> None:
        """Record an event to be published when the entity is saved."""
        self._events.append(event)
        self.updated_at = _utcnow()

    def pop_events(self) -> list[DomainEvent]:
        """Retrieve and clear uncommitted events."""
        events = self._events.copy()
        self._events.clear()
        return events

    def mark_policy_rejected(self, reason: str) -> None:
        """Reject a request that failed policy evaluation (terminal)."""
        if self.state != RequestState.SUBMITTED:
            raise StateTransitionError(
                "Cannot policy-reject request",
                current_state=self.state,
                attempted_state=RequestState.POLICY_REJECTED,
            )
        self.state = RequestState.POLICY_REJECTED
        self.provisioning_error = reason
        self.updated_at = _utcnow()

    def require_approval(self) -> None:
        """Transition to pending approval state."""
        if self.state != RequestState.SUBMITTED:
            raise StateTransitionError(
                "Cannot require approval",
                current_state=self.state,
                attempted_state=RequestState.PENDING_APPROVAL,
            )
        self.state = RequestState.PENDING_APPROVAL
        self.updated_at = _utcnow()

    def cancel(self, reason: str) -> None:
        """Cancel a request awaiting approval — e.g. the approval window expired."""
        if self.state not in (RequestState.SUBMITTED, RequestState.PENDING_APPROVAL):
            raise StateTransitionError(
                "Cannot cancel request",
                current_state=self.state,
                attempted_state=RequestState.CANCELLED,
            )
        self.state = RequestState.CANCELLED
        self.provisioning_error = reason
        self.updated_at = _utcnow()

    def approve(self, approved_by: str) -> None:
        """Approve the request and mark ready for provisioning."""
        if self.state not in (RequestState.SUBMITTED, RequestState.PENDING_APPROVAL):
            raise StateTransitionError(
                "Cannot approve request in current state",
                current_state=self.state,
                attempted_state=RequestState.APPROVED,
            )
        self.state = RequestState.APPROVED
        self._record_event(
            AccountRequestApproved(request_id=self.request_id, approved_by=approved_by)
        )

    def reject(self, reason: str) -> None:
        """Reject the account request."""
        if self.state != RequestState.PENDING_APPROVAL:
            raise StateTransitionError(
                "Cannot reject request",
                current_state=self.state,
                attempted_state=RequestState.REJECTED,
            )
        self.state = RequestState.REJECTED
        self.updated_at = _utcnow()

    def start_provisioning(self, execution_id: str) -> None:
        """Transition to provisioning state."""
        if self.state != RequestState.APPROVED:
            raise StateTransitionError(
                "Cannot start provisioning",
                current_state=self.state,
                attempted_state=RequestState.PROVISIONING,
            )
        self.state = RequestState.PROVISIONING
        self._record_event(
            AccountProvisioningStarted(
                request_id=self.request_id, workflow_execution_id=execution_id
            )
        )

    def record_account_created(self, aws_account_id: str) -> None:
        """Persist the created account id mid-workflow (stays in PROVISIONING).

        Recording the id before later steps run makes the workflow resumable: an
        interrupted execution can be re-driven without re-creating the account.
        """
        if self.state != RequestState.PROVISIONING:
            raise StateTransitionError(
                "Cannot record account before provisioning",
                current_state=self.state,
                attempted_state=RequestState.PROVISIONING,
            )
        self.aws_account_id = aws_account_id
        self.updated_at = _utcnow()

    def mark_completed(self, aws_account_id: str) -> None:
        """Mark provisioning as successful."""
        if self.state != RequestState.PROVISIONING:
            raise StateTransitionError(
                "Cannot complete request",
                current_state=self.state,
                attempted_state=RequestState.COMPLETED,
            )
        self.state = RequestState.COMPLETED
        self.aws_account_id = aws_account_id
        elapsed = (_utcnow() - self.created_at).total_seconds()
        self._record_event(
            AccountProvisioningCompleted(
                request_id=self.request_id,
                account_id=aws_account_id,
                elapsed_seconds=elapsed,
            )
        )

    def mark_action_required(self, step: str, reason: str, aws_account_id: str) -> None:
        """Mark provisioning as partially failed, requiring human remediation.

        The AWS account was created but its baseline could not be fully applied.
        The account is quarantined; a platform engineer must fix the underlying
        cause and re-drive the workflow. This is a terminal-from-automation
        state that still counts toward the SLO's ``ACTION_REQUIRED`` bucket.
        """
        if self.state != RequestState.PROVISIONING:
            raise StateTransitionError(
                "Cannot mark action required",
                current_state=self.state,
                attempted_state=RequestState.ACTION_REQUIRED,
            )
        self.state = RequestState.ACTION_REQUIRED
        self.aws_account_id = aws_account_id
        self.provisioning_error = f"{step}: {reason}"
        self._record_event(
            AccountProvisioningFailed(
                request_id=self.request_id, failed_step=step, error_message=reason
            )
        )

    def mark_failed(self, step: str, error: str) -> None:
        """Mark provisioning as a hard failure (no account created)."""
        if self.state != RequestState.PROVISIONING:
            raise StateTransitionError(
                "Cannot fail request",
                current_state=self.state,
                attempted_state=RequestState.FAILED,
            )
        self.state = RequestState.FAILED
        self.provisioning_error = f"{step}: {error}"
        self._record_event(
            AccountProvisioningFailed(
                request_id=self.request_id, failed_step=step, error_message=error
            )
        )
