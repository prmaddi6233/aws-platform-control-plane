"""Behavioural tests for the ControlPlaneService orchestration."""

from datetime import UTC, datetime, timedelta

import pytest

from platform_control_plane.api.schemas import CreateAccountRequest
from platform_control_plane.config import ControlPlaneConfig
from platform_control_plane.domain.account_request import RequestState
from platform_control_plane.domain.audit import AuditAction
from platform_control_plane.exceptions import ApprovalError, IdempotencyConflictError
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository
from platform_control_plane.services.control_plane import ControlPlaneService


def _create(**overrides) -> CreateAccountRequest:
    base = dict(
        idempotency_key="idem-key-svc-0001",
        account_name="payments-prod",
        owner="team@example.com",
        environment="production",
        data_classification="confidential",
        monthly_budget=5000,
        regions=["ca-central-1"],
    )
    base.update(overrides)
    return CreateAccountRequest(**base)


def _service(clock=None, config=None) -> ControlPlaneService:
    return ControlPlaneService(InMemoryAccountRequestRepository(), config=config, clock=clock)


def test_valid_submit_moves_to_pending_approval():
    svc = _service()
    req = svc.submit(_create())
    assert req.state == RequestState.PENDING_APPROVAL
    actions = {e.action for e in svc.audit_trail(req.request_id)}
    assert AuditAction.REQUEST_SUBMITTED in actions
    assert AuditAction.POLICY_EVALUATED in actions


def test_policy_denied_submit_is_rejected_and_audited():
    svc = _service()
    req = svc.submit(_create(idempotency_key="idem-key-badregion", regions=["ap-southeast-2"]))
    assert req.state == RequestState.POLICY_REJECTED
    actions = {e.action for e in svc.audit_trail(req.request_id)}
    assert AuditAction.POLICY_REJECTED in actions


def test_duplicate_identical_submit_returns_original():
    svc = _service()
    first = svc.submit(_create(idempotency_key="idem-key-replay"))
    second = svc.submit(_create(idempotency_key="idem-key-replay"))
    assert second.request_id == first.request_id
    actions = [e.action for e in svc.audit_trail(first.request_id)]
    assert AuditAction.IDEMPOTENT_REPLAY in actions


def test_idempotency_key_conflict_on_different_payload():
    svc = _service()
    svc.submit(_create(idempotency_key="idem-key-conflict", monthly_budget=5000))
    with pytest.raises(IdempotencyConflictError):
        svc.submit(_create(idempotency_key="idem-key-conflict", monthly_budget=9999))


def test_approve_transitions_to_approved():
    svc = _service()
    req = svc.submit(_create())
    approved = svc.approve(req.request_id, approver="alice@example.com")
    assert approved.state == RequestState.APPROVED
    actions = [e.action for e in svc.audit_trail(req.request_id)]
    assert AuditAction.APPROVAL_GRANTED in actions


def test_reject_transitions_to_rejected():
    svc = _service()
    req = svc.submit(_create())
    rejected = svc.reject(req.request_id, approver="alice@example.com", reason="not needed")
    assert rejected.state == RequestState.REJECTED


def test_approval_after_window_expiry_raises():
    # Clock reports a time well past the approval TTL.
    future = datetime.now(UTC) + timedelta(hours=3)
    svc = _service(clock=lambda: future, config=ControlPlaneConfig(approval_ttl_minutes=60))
    req = svc.submit(_create())
    with pytest.raises(ApprovalError):
        svc.approve(req.request_id, approver="alice@example.com")


def test_expire_if_stale_cancels_request():
    future = datetime.now(UTC) + timedelta(hours=3)
    svc = _service(clock=lambda: future, config=ControlPlaneConfig(approval_ttl_minutes=60))
    req = svc.submit(_create())
    cancelled = svc.expire_if_stale(req.request_id)
    assert cancelled.state == RequestState.CANCELLED
    actions = [e.action for e in svc.audit_trail(req.request_id)]
    assert AuditAction.APPROVAL_EXPIRED in actions


def test_full_provisioning_flow_completes():
    svc = _service()
    req = svc.submit(_create())
    svc.approve(req.request_id, approver="alice@example.com")
    svc.start_provisioning(req.request_id, execution_id="exec-1")
    completed = svc.complete_provisioning(req.request_id, aws_account_id="111111111111")
    assert completed.state == RequestState.COMPLETED
    assert completed.aws_account_id == "111111111111"
    actions = [e.action for e in svc.audit_trail(req.request_id)]
    assert AuditAction.PROVISIONING_COMPLETED in actions
