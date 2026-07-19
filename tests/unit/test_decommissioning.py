"""Behavioural tests for the decommissioning domain, gates, and service."""

from datetime import UTC, datetime, timedelta

import pytest

from platform_control_plane.config import ControlPlaneConfig
from platform_control_plane.domain.audit import verify_chain
from platform_control_plane.domain.decommission import (
    Commitment,
    CommitmentType,
    DecommissioningRequest,
    DecommissionState,
    FindingSeverity,
    RetainedResource,
    RetainedResourceType,
    check_residual_spend,
    discover_dependencies,
    review_commitments,
)
from platform_control_plane.exceptions import (
    ActiveDecommissionExistsError,
    AuthorizationError,
    DuplicateRequestError,
    IdempotencyConflictError,
    StateTransitionError,
)
from platform_control_plane.repositories.decommission_memory import (
    InMemoryDecommissioningRepository,
)
from platform_control_plane.services.decommissioning import (
    DecommissioningService,
    StaticDecommissionDataSource,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)
ACCOUNT = "111111111111"


def _commitment(cid, *, cancellable, months_left, monthly=100.0, ctype=CommitmentType.SAVINGS_PLAN):
    return Commitment(
        commitment_id=cid,
        commitment_type=ctype,
        monthly_cost=monthly,
        end_date=NOW + timedelta(days=30 * months_left),
        cancellable=cancellable,
    )


def _service(source=None, clock=None):
    return DecommissioningService(
        InMemoryDecommissioningRepository(),
        source or StaticDecommissionDataSource(),
        config=ControlPlaneConfig(quarantine_retention_days=7),
        clock=clock or (lambda: NOW),
    )


def _submit(svc, key="idem-dec-0001", requested_by="owner@example.com"):
    return svc.submit(idempotency_key=key, aws_account_id=ACCOUNT, requested_by=requested_by)


# ── deterministic gates ─────────────────────────────────────────────────────
def test_review_commitments_flags_active_ri_with_traceable_cost():
    findings = review_commitments(
        [
            _commitment(
                "ri-1",
                cancellable=False,
                months_left=6,
                monthly=100.0,
                ctype=CommitmentType.RESERVED_INSTANCE,
            )
        ],
        now=NOW,
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.finding_type == "ACTIVE_RESERVED_INSTANCE"
    assert f.severity == FindingSeverity.BLOCKING
    # 6 months remaining * 100.0 = 600.0 — traceable to inputs.
    assert f.estimated_remaining_cost == 600.0
    assert f.evidence["remaining_months"] == 6


def test_review_commitments_ignores_cancellable_and_expired():
    findings = review_commitments(
        [
            _commitment("sp-cancellable", cancellable=True, months_left=6),
            _commitment("sp-expired", cancellable=False, months_left=-1),
        ],
        now=NOW,
    )
    assert findings == []


def test_discover_dependencies_blocks_storage_warns_snapshots():
    findings = discover_dependencies(
        [
            RetainedResource(
                resource_id="vol-1", resource_type=RetainedResourceType.PERSISTENT_STORAGE
            ),
            RetainedResource(resource_id="snap-1", resource_type=RetainedResourceType.SNAPSHOT),
        ]
    )
    by_id = {f.resource_or_commitment_id: f for f in findings}
    assert by_id["vol-1"].severity == FindingSeverity.BLOCKING
    assert by_id["snap-1"].severity == FindingSeverity.WARNING


def test_residual_spend_uses_trailing_window():
    above = check_residual_spend([500.0, 4.0, 5.0, 6.0], lookback_days=3, threshold_daily=1.0)
    assert above is not None and above.severity == FindingSeverity.BLOCKING
    below = check_residual_spend([100.0, 0.0, 0.1, 0.0], lookback_days=3, threshold_daily=1.0)
    assert below is None


# ── service: happy path ──────────────────────────────────────────────────────
def test_valid_decommission_reaches_awaiting_approval():
    svc = _service()
    req = _submit(svc)
    assert req.state == DecommissionState.DECOMMISSION_REQUESTED
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.AWAITING_DECOMMISSION_APPROVAL


def test_full_lifecycle_quarantine_then_close():
    svc = _service()
    req = _submit(svc)
    svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    req = svc.approve_and_quarantine(req.decommission_id, approver="approver@example.com")
    assert req.state == DecommissionState.RETENTION_PERIOD

    # Retention not elapsed → stays.
    svc.close_if_elapsed(req.decommission_id)
    assert svc.get(req.decommission_id).state == DecommissionState.RETENTION_PERIOD

    # After retention → CLOSED.
    later = DecommissioningService(
        svc._repo, StaticDecommissionDataSource(), clock=lambda: NOW + timedelta(days=8)
    )
    req = later.close_if_elapsed(req.decommission_id)
    assert req.state == DecommissionState.CLOSED


# ── service: authorization & gates ───────────────────────────────────────────
def test_unauthorized_self_authorization_rejected():
    svc = _service()
    req = _submit(svc, requested_by="same@example.com")
    with pytest.raises(AuthorizationError):
        svc.run_reviews(req.decommission_id, authorizer="same@example.com")


def test_active_commitment_blocks_and_prevents_quarantine():
    source = StaticDecommissionDataSource(
        commitments=[
            _commitment(
                "ri-1",
                cancellable=False,
                months_left=6,
                monthly=800.0,
                ctype=CommitmentType.RESERVED_INSTANCE,
            )
        ]
    )
    svc = _service(source)
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.DECOMMISSION_ACTION_REQUIRED
    # Cannot approve/quarantine while blocked.
    with pytest.raises(StateTransitionError):
        svc.approve_and_quarantine(req.decommission_id, approver="approver@example.com")


def test_active_savings_plan_exposure_blocks():
    source = StaticDecommissionDataSource(
        commitments=[_commitment("sp-1", cancellable=False, months_left=3, monthly=300.0)]
    )
    svc = _service(source)
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.DECOMMISSION_ACTION_REQUIRED
    assert any("ACTIVE_SAVINGS_PLAN" in f.finding_type for f in req.commitment_findings)


def test_active_dependencies_block():
    source = StaticDecommissionDataSource(
        retained_resources=[
            RetainedResource(
                resource_id="rds-1", resource_type=RetainedResourceType.RESIDUAL_COMPUTE
            )
        ]
    )
    svc = _service(source)
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.DECOMMISSION_ACTION_REQUIRED


def test_residual_spend_above_threshold_blocks():
    svc = _service(StaticDecommissionDataSource(daily_spend=[50.0, 40.0, 60.0]))
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.DECOMMISSION_ACTION_REQUIRED


def test_residual_spend_below_threshold_passes():
    svc = _service(StaticDecommissionDataSource(daily_spend=[0.0, 0.1, 0.0]))
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.AWAITING_DECOMMISSION_APPROVAL


def test_explicit_risk_acceptance_allows_progression():
    source = StaticDecommissionDataSource(
        commitments=[_commitment("ri-1", cancellable=False, months_left=6, monthly=800.0)]
    )
    svc = _service(source)
    req = _submit(svc)
    req = svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    assert req.state == DecommissionState.DECOMMISSION_ACTION_REQUIRED
    req = svc.accept_risk(
        req.decommission_id,
        accepted_by="director@example.com",
        justification="budget owner accepts",
    )
    assert req.state == DecommissionState.AWAITING_DECOMMISSION_APPROVAL


# ── service: failure paths ───────────────────────────────────────────────────
def test_evidence_export_failure_fails_workflow():
    svc = _service()
    req = _submit(svc)
    svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    req = svc.approve_and_quarantine(
        req.decommission_id, approver="approver@example.com", evidence_ok=False
    )
    assert req.state == DecommissionState.DECOMMISSION_FAILED
    assert req.failure_reason == "Evidence export failed"


def test_workload_disablement_failure_fails_workflow():
    svc = _service()
    req = _submit(svc)
    svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    req = svc.approve_and_quarantine(
        req.decommission_id, approver="approver@example.com", workloads_ok=False
    )
    assert req.state == DecommissionState.DECOMMISSION_FAILED
    assert req.failure_reason == "Workload disablement failed"


def test_premature_closure_attempt_rejected():
    svc = _service()
    req = _submit(svc)
    svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    svc.approve_and_quarantine(req.decommission_id, approver="approver@example.com")
    # Retention not elapsed → direct mark-ready must raise.
    loaded = svc.get(req.decommission_id)
    with pytest.raises(StateTransitionError):
        loaded.mark_ready_for_closure(now=NOW)


# ── idempotency & persistence ────────────────────────────────────────────────
def test_duplicate_request_same_key_returns_original():
    svc = _service()
    first = _submit(svc, key="idem-dup")
    second = _submit(svc, key="idem-dup")
    assert second.decommission_id == first.decommission_id


def test_replayed_event_audited_as_replay():
    from platform_control_plane.domain.audit import AuditAction

    svc = _service()
    first = _submit(svc, key="idem-replay")
    _submit(svc, key="idem-replay")
    actions = [e.action for e in svc.audit_trail(first.decommission_id)]
    assert AuditAction.IDEMPOTENT_REPLAY in actions


def test_idempotency_conflict_on_different_payload():
    svc = _service()
    svc.submit(
        idempotency_key="idem-x", aws_account_id=ACCOUNT, requested_by="o@example.com", reason="a"
    )
    # Close it out so the account-active guard doesn't fire first.
    with pytest.raises(IdempotencyConflictError):
        svc.submit(
            idempotency_key="idem-x",
            aws_account_id=ACCOUNT,
            requested_by="o@example.com",
            reason="b",
        )


def test_second_active_decommission_for_account_rejected():
    svc = _service()
    _submit(svc, key="idem-a")
    with pytest.raises(ActiveDecommissionExistsError):
        _submit(svc, key="idem-b")


def test_repository_rejects_duplicate_idempotency_key_directly():
    repo = InMemoryDecommissioningRepository()
    r1 = DecommissioningRequest(
        idempotency_key="k1", aws_account_id="222222222222", requested_by="o"
    )
    repo.create(r1)
    r2 = DecommissioningRequest(
        idempotency_key="k1", aws_account_id="333333333333", requested_by="o"
    )
    with pytest.raises(DuplicateRequestError):
        repo.create(r2)


# ── invalid transitions & audit history ──────────────────────────────────────
def test_invalid_state_transition_rejected():
    req = DecommissioningRequest(idempotency_key="k", aws_account_id=ACCOUNT, requested_by="o")
    with pytest.raises(StateTransitionError):
        req.complete_closure()  # cannot close from REQUESTED


def test_audit_history_recorded_for_every_transition():
    svc = _service()
    req = _submit(svc)
    svc.run_reviews(req.decommission_id, authorizer="approver@example.com")
    svc.approve_and_quarantine(req.decommission_id, approver="approver@example.com")
    reloaded = svc.get(req.decommission_id)
    audit = svc.audit_trail(req.decommission_id)
    # One audit event per FSM transition plus the initial submit event.
    assert len(audit) == len(reloaded.history) + 1
    # Chain must be intact and tamper-evident.
    assert verify_chain(audit) is True
