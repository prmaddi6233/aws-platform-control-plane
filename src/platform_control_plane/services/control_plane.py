"""Application service orchestrating the account request lifecycle.

This is the seam the CLI and (future) API handlers call. It composes the domain
model, the policy engine, and a repository, and writes an audit event for every
material decision. It contains no AWS SDK calls — provisioning is represented by
a domain transition here and delegated to Step Functions in a real deployment
(see docs/production-boundaries.md).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from platform_control_plane.config import ControlPlaneConfig
from platform_control_plane.domain.account_request import AccountRequest, RequestState
from platform_control_plane.domain.audit import AuditAction, AuditEvent
from platform_control_plane.exceptions import (
    ApprovalError,
    IdempotencyConflictError,
)
from platform_control_plane.policies.engine import PolicyEngine
from platform_control_plane.telemetry.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from platform_control_plane.api.schemas import CreateAccountRequest
    from platform_control_plane.repositories.base import AbstractAccountRequestRepository

logger = get_logger(__name__)


def _fingerprint(request: CreateAccountRequest) -> str:
    """Stable hash of the semantic payload, excluding the idempotency key."""
    payload = request.model_dump(exclude={"idempotency_key"})
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ControlPlaneService:
    """Coordinates request submission, policy, approval, and provisioning."""

    def __init__(
        self,
        repository: AbstractAccountRequestRepository,
        *,
        config: ControlPlaneConfig | None = None,
        policy_engine: PolicyEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._config = config or ControlPlaneConfig()
        self._policy = policy_engine or PolicyEngine()
        self._now = clock or (lambda: datetime.now(UTC))

    # ── submission ─────────────────────────────────────────────────────────
    def submit(self, create: CreateAccountRequest, *, actor: str = "system") -> AccountRequest:
        """Submit a request. Idempotent on the idempotency key.

        - Same key + identical payload -> returns the original request (replay).
        - Same key + different payload -> IdempotencyConflictError.
        - New key -> policy evaluation; on denial the request is persisted in
          POLICY_REJECTED for auditability; on allow it moves to PENDING_APPROVAL.
        """
        fingerprint = _fingerprint(create)
        existing = self._repo.get_by_idempotency_key(create.idempotency_key)
        if existing is not None:
            if existing.payload_fingerprint == fingerprint:
                self._repo.append_audit(
                    AuditEvent(
                        request_id=existing.request_id,
                        action=AuditAction.IDEMPOTENT_REPLAY,
                        actor=actor,
                        detail={"idempotency_key": create.idempotency_key},
                    )
                )
                return existing
            raise IdempotencyConflictError(
                "Idempotency key reused with a different payload.",
                idempotency_key=create.idempotency_key,
                existing_request_id=existing.request_id,
            )

        request = AccountRequest(
            idempotency_key=create.idempotency_key,
            account_name=create.account_name,
            owner=create.owner,
            environment=create.environment,
            data_classification=create.data_classification,
            monthly_budget=create.monthly_budget,
            regions=create.regions,
            payload_fingerprint=fingerprint,
        )
        self._repo.create(request)
        self._repo.append_audit(
            AuditEvent(
                request_id=request.request_id,
                action=AuditAction.REQUEST_SUBMITTED,
                actor=actor,
                detail={"account_name": request.account_name},
            )
        )

        evaluation = self._policy.evaluate(create)
        _, version = self._repo.get(request.request_id)
        if not evaluation.allowed:
            request.mark_policy_rejected(evaluation.summary)
            self._repo.update(request, expected_version=version)
            self._repo.append_audit(
                AuditEvent(
                    request_id=request.request_id,
                    action=AuditAction.POLICY_REJECTED,
                    actor=actor,
                    detail={"denials": [d.model_dump() for d in evaluation.denials]},
                )
            )
            return request

        request.require_approval()
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request.request_id,
                action=AuditAction.POLICY_EVALUATED,
                actor=actor,
                detail={"result": "ALLOW"},
            )
        )
        return request

    # ── approval ───────────────────────────────────────────────────────────
    def approve(self, request_id: str, *, approver: str) -> AccountRequest:
        request, version = self._repo.get(request_id)
        self._guard_approval_window(request)
        request.approve(approver)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.APPROVAL_GRANTED,
                actor=approver,
            )
        )
        return request

    def reject(self, request_id: str, *, approver: str, reason: str) -> AccountRequest:
        request, version = self._repo.get(request_id)
        if request.state == RequestState.SUBMITTED:
            request.require_approval()
        request.reject(reason)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.APPROVAL_REJECTED,
                actor=approver,
                detail={"reason": reason},
            )
        )
        return request

    def expire_if_stale(self, request_id: str, *, actor: str = "scheduler") -> AccountRequest:
        """Cancel a request whose approval window has elapsed."""
        request, version = self._repo.get(request_id)
        if request.state != RequestState.PENDING_APPROVAL:
            return request
        if not self._approval_expired(request):
            return request
        request.cancel("approval window expired")
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.APPROVAL_EXPIRED,
                actor=actor,
            )
        )
        return request

    # ── provisioning (simulated) ─────────────────────────────────────────────
    def start_provisioning(self, request_id: str, execution_id: str) -> AccountRequest:
        request, version = self._repo.get(request_id)
        request.start_provisioning(execution_id)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.PROVISIONING_STARTED,
                actor="workflow",
                detail={"execution_id": execution_id},
            )
        )
        return request

    def record_account_created(self, request_id: str, aws_account_id: str) -> AccountRequest:
        """Persist the created account id while provisioning continues."""
        request, version = self._repo.get(request_id)
        if request.aws_account_id == aws_account_id:
            return request
        request.record_account_created(aws_account_id)
        self._repo.update(request, expected_version=version)
        return request

    def complete_provisioning(self, request_id: str, aws_account_id: str) -> AccountRequest:
        request, version = self._repo.get(request_id)
        request.mark_completed(aws_account_id)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.PROVISIONING_COMPLETED,
                actor="workflow",
                detail={"aws_account_id": aws_account_id},
            )
        )
        return request

    def require_action(
        self, request_id: str, *, step: str, reason: str, aws_account_id: str
    ) -> AccountRequest:
        """Account created but baseline incomplete — hold for remediation."""
        request, version = self._repo.get(request_id)
        request.mark_action_required(step, reason, aws_account_id)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.PROVISIONING_FAILED,
                actor="workflow",
                detail={"step": step, "reason": reason, "outcome": "ACTION_REQUIRED"},
            )
        )
        return request

    def fail_provisioning(self, request_id: str, *, step: str, error: str) -> AccountRequest:
        """Hard provisioning failure with no account created."""
        request, version = self._repo.get(request_id)
        request.mark_failed(step, error)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=request_id,
                action=AuditAction.PROVISIONING_FAILED,
                actor="workflow",
                detail={"step": step, "error": error, "outcome": "FAILED"},
            )
        )
        return request

    # ── queries ──────────────────────────────────────────────────────────────
    def get(self, request_id: str) -> AccountRequest:
        request, _ = self._repo.get(request_id)
        return request

    def list_requests(self) -> list[AccountRequest]:
        return self._repo.list_all()

    def audit_trail(self, request_id: str) -> list[AuditEvent]:
        return self._repo.get_audit(request_id)

    # ── helpers ────────────────────────────────────────────────────────────
    def _approval_expired(self, request: AccountRequest) -> bool:
        deadline = request.created_at + self._config.approval_ttl
        return self._now() > deadline

    def _guard_approval_window(self, request: AccountRequest) -> None:
        if request.state == RequestState.PENDING_APPROVAL and self._approval_expired(request):
            raise ApprovalError(
                "Approval window has expired; the request must be resubmitted.",
                details={
                    "request_id": request.request_id,
                    "created_at": request.created_at.isoformat(),
                    "ttl": str(self._config.approval_ttl),
                },
            )
