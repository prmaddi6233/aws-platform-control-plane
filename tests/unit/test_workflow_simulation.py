"""Failure-path tests for the provisioning workflow simulation."""

import pytest

from platform_control_plane.api.schemas import CreateAccountRequest
from platform_control_plane.domain.account_request import RequestState
from platform_control_plane.exceptions import (
    RetryableStepError,
    StateTransitionError,
    WorkflowStepError,
)
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository
from platform_control_plane.services.control_plane import ControlPlaneService
from platform_control_plane.workflows.simulation import (
    ProvisioningSimulator,
    RetryPolicy,
    WorkflowStep,
    default_steps,
)


def _approved_service() -> tuple[ControlPlaneService, str]:
    svc = ControlPlaneService(InMemoryAccountRequestRepository())
    req = svc.submit(
        CreateAccountRequest(
            idempotency_key="idem-key-wf-0001",
            account_name="payments-prod",
            owner="team@example.com",
            environment="production",
            data_classification="confidential",
            monthly_budget=5000,
            regions=["ca-central-1"],
        )
    )
    svc.approve(req.request_id, approver="alice@example.com")
    return svc, req.request_id


def _create_account(ctx: dict) -> None:
    ctx["aws_account_id"] = ctx.get("aws_account_id") or "111111111111"


def _ok(ctx: dict) -> None:
    return None


def test_happy_path_completes():
    svc, rid = _approved_service()
    report = ProvisioningSimulator(svc).run(rid)
    assert report.terminal_state == RequestState.COMPLETED
    assert report.dlq == []
    assert all(s.status == "SUCCESS" for s in report.steps)
    assert svc.get(rid).aws_account_id == "111111111111"


def test_retryable_failure_then_success():
    calls = {"n": 0}

    def flaky_baseline(ctx: dict) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableStepError("throttled", step_name="ApplyBaseline")

    steps = [
        WorkflowStep("CreateAccount", _create_account, RetryPolicy(5), post_account_creation=False),
        WorkflowStep("ApplyBaseline", flaky_baseline, RetryPolicy(5)),
        WorkflowStep("AssignIdentity", _ok, RetryPolicy(1)),
    ]
    svc, rid = _approved_service()
    report = ProvisioningSimulator(svc, steps).run(rid)
    assert report.terminal_state == RequestState.COMPLETED
    baseline = next(s for s in report.steps if s.step == "ApplyBaseline")
    assert baseline.attempts == 3  # two retries then success


def test_retry_exhaustion_yields_action_required_and_dlq():
    def always_throttled(ctx: dict) -> None:
        raise RetryableStepError("throttled", step_name="ApplyBaseline")

    steps = [
        WorkflowStep("CreateAccount", _create_account, RetryPolicy(3), post_account_creation=False),
        WorkflowStep("ApplyBaseline", always_throttled, RetryPolicy(3)),
    ]
    svc, rid = _approved_service()
    report = ProvisioningSimulator(svc, steps).run(rid)
    # Account was created, baseline never applied → ACTION_REQUIRED, not FAILED.
    assert report.terminal_state == RequestState.ACTION_REQUIRED
    assert report.failed_step == "ApplyBaseline"
    assert report.dlq and report.dlq[0]["step"] == "ApplyBaseline"
    assert svc.get(rid).state == RequestState.ACTION_REQUIRED


def test_permanent_create_account_failure_is_failed_no_account():
    def boom(ctx: dict) -> None:
        raise WorkflowStepError("quota exceeded", step_name="CreateAccount")

    steps = [WorkflowStep("CreateAccount", boom, RetryPolicy(5), post_account_creation=False)]
    svc, rid = _approved_service()
    report = ProvisioningSimulator(svc, steps).run(rid)
    assert report.terminal_state == RequestState.FAILED
    assert svc.get(rid).aws_account_id is None
    assert report.dlq[0]["step"] == "CreateAccount"


def test_partial_baseline_failure_is_action_required():
    def baseline_denied(ctx: dict) -> None:
        raise WorkflowStepError("SCP denied Config", step_name="ApplyBaseline")

    steps = [
        WorkflowStep("CreateAccount", _create_account, RetryPolicy(5), post_account_creation=False),
        WorkflowStep("ApplyBaseline", baseline_denied, RetryPolicy(3)),
    ]
    svc, rid = _approved_service()
    report = ProvisioningSimulator(svc, steps).run(rid)
    assert report.terminal_state == RequestState.ACTION_REQUIRED
    assert svc.get(rid).aws_account_id == "111111111111"


def test_recovery_after_interruption_skips_account_creation():
    svc, rid = _approved_service()
    # Simulate an interruption: provisioning started, account created, then crash.
    svc.start_provisioning(rid, "exec-interrupted")
    svc.record_account_created(rid, "111111111111")
    assert svc.get(rid).state == RequestState.PROVISIONING

    def must_not_run(ctx: dict) -> None:
        raise AssertionError("CreateAccount must not run on resume")

    steps = [
        WorkflowStep("CreateAccount", must_not_run, RetryPolicy(5), post_account_creation=False),
        WorkflowStep("ApplyBaseline", _ok, RetryPolicy(3)),
        WorkflowStep("AssignIdentity", _ok, RetryPolicy(1)),
    ]
    report = ProvisioningSimulator(svc, steps).run(rid)
    assert report.terminal_state == RequestState.COMPLETED
    create_step = next(s for s in report.steps if s.step == "CreateAccount")
    assert create_step.attempts == 0  # skipped, account already existed


def test_default_steps_mirror_asl_sequence():
    names = [s.name for s in default_steps()]
    assert names == [
        "CreateAccount",
        "ApplyBaseline",
        "AssignIdentity",
        "CreateBudget",
        "NotifyOwner",
    ]


@pytest.mark.parametrize("bad_regions", [["ap-southeast-2"]])
def test_policy_rejected_request_never_reaches_provisioning(bad_regions):
    svc = ControlPlaneService(InMemoryAccountRequestRepository())
    req = svc.submit(
        CreateAccountRequest(
            idempotency_key="idem-key-wf-reject",
            account_name="payments-prod",
            owner="team@example.com",
            environment="production",
            data_classification="public",
            monthly_budget=5000,
            regions=bad_regions,
        )
    )
    assert req.state == RequestState.POLICY_REJECTED
    # A rejected request cannot be provisioned (start_provisioning is illegal).
    with pytest.raises(StateTransitionError):
        ProvisioningSimulator(svc).run(req.request_id)
