"""Application service orchestrating the account decommissioning lifecycle.

Drives the :class:`DecommissioningRequest` FSM through the deterministic
pre-closure gates and the quarantine retention period, persisting every step and
writing one audit event per state transition. Gate inputs come from a
:class:`DecommissionDataSource`; the static implementation is used locally and
in tests, and a real Cost Explorer / CUR-FOCUS / Organizations integration would
implement the same protocol. No AWS SDK calls live here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from platform_control_plane.config import ControlPlaneConfig
from platform_control_plane.domain.audit import AuditAction, AuditEvent
from platform_control_plane.domain.decommission import (
    Commitment,
    DecommissioningRequest,
    DecommissionState,
    EvidenceExport,
    RetainedResource,
    check_residual_spend,
    discover_dependencies,
    review_commitments,
)
from platform_control_plane.domain.failures import FailureClass
from platform_control_plane.exceptions import IdempotencyConflictError
from platform_control_plane.telemetry.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from platform_control_plane.repositories.decommission_base import (
        AbstractDecommissioningRequestRepository,
    )

logger = get_logger(__name__)


class DecommissionDataSource(Protocol):
    """Interface for the data a decommission needs. Implemented by a live
    Cost Explorer / CUR-FOCUS / Organizations integration in production."""

    def list_retained_resources(self, aws_account_id: str) -> list[RetainedResource]: ...
    def list_commitments(self, aws_account_id: str) -> list[Commitment]: ...
    def recent_daily_spend(self, aws_account_id: str) -> list[float]: ...


@dataclass
class StaticDecommissionDataSource:
    """Deterministic in-memory data source for local demo and tests."""

    retained_resources: list[RetainedResource] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    daily_spend: list[float] = field(default_factory=list)

    def list_retained_resources(self, aws_account_id: str) -> list[RetainedResource]:
        return list(self.retained_resources)

    def list_commitments(self, aws_account_id: str) -> list[Commitment]:
        return list(self.commitments)

    def recent_daily_spend(self, aws_account_id: str) -> list[float]:
        return list(self.daily_spend)


def _fingerprint(aws_account_id: str, reason: str) -> str:
    encoded = json.dumps({"account": aws_account_id, "reason": reason}, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


class DecommissioningService:
    """Coordinates authorization, pre-closure gates, quarantine, and closure."""

    def __init__(
        self,
        repository: AbstractDecommissioningRequestRepository,
        data_source: DecommissionDataSource,
        *,
        config: ControlPlaneConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._data = data_source
        self._config = config or ControlPlaneConfig()
        self._now = clock or (lambda: datetime.now(UTC))

    # ── internal transition helper (persist + audit every transition) ────────
    def _transition(
        self,
        decommission_id: str,
        mutate: Callable[[DecommissioningRequest], None],
        *,
        action: AuditAction,
        actor: str,
        detail: dict[str, Any] | None = None,
    ) -> DecommissioningRequest:
        request, version = self._repo.get(decommission_id)
        before = request.state
        mutate(request)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=decommission_id,
                action=action,
                actor=actor,
                correlation_id=request.correlation_id,
                request_version=version + 1,
                detail={"from": before.value, "to": request.state.value, **(detail or {})},
            )
        )
        return request

    # ── submission ───────────────────────────────────────────────────────────
    def submit(
        self,
        *,
        idempotency_key: str,
        aws_account_id: str,
        requested_by: str,
        reason: str = "",
    ) -> DecommissioningRequest:
        """Idempotent submission. A replay with the same key returns the original."""
        fingerprint = _fingerprint(aws_account_id, reason)
        existing = self._repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if _fingerprint(existing.aws_account_id, existing.reason) == fingerprint:
                self._repo.append_audit(
                    AuditEvent(
                        request_id=existing.decommission_id,
                        action=AuditAction.IDEMPOTENT_REPLAY,
                        actor=requested_by,
                        correlation_id=existing.correlation_id,
                        detail={"idempotency_key": idempotency_key},
                    )
                )
                return existing
            raise IdempotencyConflictError(
                "Idempotency key reused with a different decommission payload.",
                idempotency_key=idempotency_key,
                existing_request_id=existing.decommission_id,
            )

        request = DecommissioningRequest(
            idempotency_key=idempotency_key,
            aws_account_id=aws_account_id,
            requested_by=requested_by,
            reason=reason,
        )
        self._repo.create(request)
        self._repo.append_audit(
            AuditEvent(
                request_id=request.decommission_id,
                action=AuditAction.DECOMMISSION_REQUESTED,
                actor=requested_by,
                correlation_id=request.correlation_id,
                request_version=1,
                detail={"aws_account_id": aws_account_id},
            )
        )
        return request

    # ── review phase ─────────────────────────────────────────────────────────
    def run_reviews(self, decommission_id: str, *, authorizer: str) -> DecommissioningRequest:
        """Authorize and run dependency/commitment/residual gates.

        Ends in ``AWAITING_DECOMMISSION_APPROVAL`` (clear) or
        ``DECOMMISSION_ACTION_REQUIRED`` (a blocking finding, no risk accepted).
        """
        request = self._repo.get(decommission_id)[0]
        account_id = request.aws_account_id

        self._transition(
            decommission_id,
            lambda r: r.begin_validation(authorizer),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor=authorizer,
        )

        deps = discover_dependencies(self._data.list_retained_resources(account_id))
        self._transition(
            decommission_id,
            lambda r: r.record_dependencies(deps),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
            detail={"dependency_findings": len(deps)},
        )

        commitments = review_commitments(self._data.list_commitments(account_id), now=self._now())
        self._transition(
            decommission_id,
            lambda r: r.record_commitments(commitments),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
            detail={"commitment_findings": len(commitments)},
        )

        residual = check_residual_spend(
            self._data.recent_daily_spend(account_id),
            lookback_days=self._config.residual_lookback_days,
            threshold_daily=self._config.residual_threshold_daily,
        )
        request = self._transition(
            decommission_id,
            lambda r: r.record_residual(residual),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
            detail={"residual_detected": residual is not None},
        )

        if request.is_blocked():
            return self._transition(
                decommission_id,
                lambda r: r.block(),
                action=AuditAction.DECOMMISSION_BLOCKED,
                actor="system",
            )
        return self._transition(
            decommission_id,
            lambda r: r.request_approval(),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
        )

    def accept_risk(
        self, decommission_id: str, *, accepted_by: str, justification: str
    ) -> DecommissioningRequest:
        request, version = self._repo.get(decommission_id)
        request.accept_risk(accepted_by, justification)
        self._repo.update(request, expected_version=version)
        self._repo.append_audit(
            AuditEvent(
                request_id=decommission_id,
                action=AuditAction.DECOMMISSION_STATE_CHANGED,
                actor=accepted_by,
                correlation_id=request.correlation_id,
                request_version=version + 1,
                detail={"risk_accepted": True, "justification": justification},
            )
        )
        # Re-drive from ACTION_REQUIRED to approval now that risk is accepted.
        return self._transition(
            decommission_id,
            lambda r: r.request_approval(),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor=accepted_by,
        )

    # ── approval → quarantine ────────────────────────────────────────────────
    def approve_and_quarantine(
        self,
        decommission_id: str,
        *,
        approver: str,
        evidence_ok: bool = True,
        workloads_ok: bool = True,
    ) -> DecommissioningRequest:
        """Approve closure, export evidence, disable workloads, enter quarantine.

        ``evidence_ok=False`` / ``workloads_ok=False`` inject a failure at that
        step (used by tests and the simulator) which fails the workflow.
        """
        self._transition(
            decommission_id,
            lambda r: r.approve_closure(approver),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor=approver,
        )

        if not evidence_ok:
            # Evidence export failed → hard fail (permanent dependency failure).
            return self._transition(
                decommission_id,
                lambda r: r.fail(
                    "Evidence export failed",
                    failure_class=FailureClass.PERMANENT_DEPENDENCY_FAILURE,
                ),
                action=AuditAction.DECOMMISSION_STATE_CHANGED,
                actor="system",
                detail={"failure": "evidence_export"},
            )

        export = EvidenceExport(
            location=f"s3://example-evidence/{decommission_id}.json",
            sha256=hashlib.sha256(decommission_id.encode()).hexdigest(),
            item_count=1,
        )
        self._transition(
            decommission_id,
            lambda r: r.record_evidence(export),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
        )

        if not workloads_ok:
            return self._transition(
                decommission_id,
                lambda r: r.fail(
                    "Workload disablement failed",
                    failure_class=FailureClass.PERMANENT_DEPENDENCY_FAILURE,
                ),
                action=AuditAction.DECOMMISSION_STATE_CHANGED,
                actor="system",
                detail={"failure": "disable_workloads"},
            )

        self._transition(
            decommission_id,
            lambda r: r.disable_workloads(),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
        )
        until = self._now() + self._config.quarantine_retention
        return self._transition(
            decommission_id,
            lambda r: r.enter_quarantine(until, now=self._now()),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
            detail={"quarantine_until": until.isoformat()},
        )

    def fail(
        self,
        decommission_id: str,
        *,
        reason: str,
        failure_class: FailureClass,
        actor: str = "system",
    ) -> DecommissioningRequest:
        return self._transition(
            decommission_id,
            lambda r: r.fail(reason, failure_class=failure_class),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor=actor,
            detail={"reason": reason, "failure_class": failure_class.value},
        )

    # ── closure ──────────────────────────────────────────────────────────────
    def close_if_elapsed(self, decommission_id: str) -> DecommissioningRequest:
        """Close the account if quarantined and the retention period elapsed."""
        request, _ = self._repo.get(decommission_id)
        if request.state != DecommissionState.RETENTION_PERIOD:
            return request
        if request.quarantine is not None and self._now() < request.quarantine.quarantine_until:
            return request

        self._transition(
            decommission_id,
            lambda r: r.mark_ready_for_closure(now=self._now()),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
        )
        self._transition(
            decommission_id,
            lambda r: r.begin_closure(),
            action=AuditAction.DECOMMISSION_STATE_CHANGED,
            actor="system",
        )
        return self._transition(
            decommission_id,
            lambda r: r.complete_closure(),
            action=AuditAction.DECOMMISSION_CLOSED,
            actor="system",
        )

    # ── queries ──────────────────────────────────────────────────────────────
    def get(self, decommission_id: str) -> DecommissioningRequest:
        return self._repo.get(decommission_id)[0]

    def audit_trail(self, decommission_id: str) -> list[AuditEvent]:
        return self._repo.get_audit(decommission_id)
