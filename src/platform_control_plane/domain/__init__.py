"""Domain events emitted during account lifecycle transitions.

Events are value objects — immutable records of something that happened.
They carry enough context for downstream consumers (audit logs, EventBridge,
notification handlers) without coupling to the domain entity internals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DomainEvent(BaseModel):
    """Base class for all domain events."""

    event_type: str
    request_id: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class AccountRequestSubmitted(DomainEvent):
    """Emitted when a new account request passes initial validation."""

    event_type: str = "AccountRequestSubmitted"
    account_name: str
    owner: str
    environment: str


class AccountRequestApproved(DomainEvent):
    """Emitted when an account request receives approval."""

    event_type: str = "AccountRequestApproved"
    approved_by: str


class AccountProvisioningStarted(DomainEvent):
    """Emitted when the provisioning workflow begins execution."""

    event_type: str = "AccountProvisioningStarted"
    workflow_execution_id: str = ""


class AccountProvisioningCompleted(DomainEvent):
    """Emitted when all provisioning steps succeed."""

    event_type: str = "AccountProvisioningCompleted"
    account_id: str
    elapsed_seconds: float = 0.0


class AccountProvisioningFailed(DomainEvent):
    """Emitted when provisioning fails after exhausting retries."""

    event_type: str = "AccountProvisioningFailed"
    failed_step: str
    error_message: str


class AccountDecommissionRequested(DomainEvent):
    """Emitted when a decommission request is submitted."""

    event_type: str = "AccountDecommissionRequested"
    account_id: str
    requested_by: str


class AccountQuarantined(DomainEvent):
    """Emitted when an account enters the quarantine holding period."""

    event_type: str = "AccountQuarantined"
    account_id: str
    quarantine_until: datetime


class AccountClosed(DomainEvent):
    """Emitted when an account is permanently closed."""

    event_type: str = "AccountClosed"
    account_id: str
