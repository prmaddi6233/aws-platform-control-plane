"""Deterministic scenario runner for the simulator CLI.

Each scenario drives the **real** domain services, repositories, policy engine,
workflow simulator, and failure taxonomy — there is no parallel implementation
that could drift from production logic. Every scenario returns a canonical
:class:`ScenarioResult` that serializes to the documented JSON contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from platform_control_plane.api.schemas import CreateAccountRequest
from platform_control_plane.config import ControlPlaneConfig
from platform_control_plane.domain.account_request import RequestState
from platform_control_plane.domain.audit import verify_chain
from platform_control_plane.domain.decommission import (
    Commitment,
    CommitmentType,
    RetainedResource,
    RetainedResourceType,
)
from platform_control_plane.domain.failures import FailureClass, profile_for
from platform_control_plane.exceptions import AuthorizationError, PlatformError
from platform_control_plane.repositories.decommission_memory import (
    InMemoryDecommissioningRepository,
)
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository
from platform_control_plane.services.control_plane import ControlPlaneService
from platform_control_plane.services.decommissioning import (
    DecommissioningService,
    StaticDecommissionDataSource,
)
from platform_control_plane.workflows.simulation import (
    ProvisioningSimulator,
    RetryPolicy,
    WorkflowStep,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA_VERSION = "1.0"

# Fixed clock so simulator output is byte-stable across runs.
FIXED_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

PROVISIONING_SCENARIOS = (
    "success",
    "policy-rejected",
    "approval-rejected",
    "approval-timeout",
    "retryable-baseline-failure",
    "retry-exhaustion",
    "manual-intervention",
)
DECOMMISSIONING_SCENARIOS = (
    "success",
    "unauthorized",
    "active-dependency",
    "active-ri",
    "active-savings-plan",
    "residual-spend-block",
    "risk-accepted",
    "evidence-export-failure",
    "workload-disablement-failure",
    "retention-not-expired",
    "closure-success",
)


class FailureInfo(BaseModel):
    classification: str
    retryable: bool
    correctable_by: str
    recommended_action: str
    alert_severity: str


class ScenarioResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    scenario: str
    workflow_type: str
    request_id: str
    correlation_id: str
    initial_state: str
    final_state: str
    success: bool
    failure: FailureInfo | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    retry_attempts: int = 0
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    audit_chain_valid: bool = True

    def exit_code(self) -> int:
        if self.success:
            return 0
        if self.failure is not None:
            return profile_for(FailureClass(self.failure.classification)).cli_exit_code
        return 2  # expected business outcome without an error (e.g. approval rejected)


def _require_fc(req: object) -> FailureClass:
    fc = getattr(req, "failure_class", None)
    assert fc is not None, "expected a failure_class on a blocked/failed request"
    return FailureClass(fc)


def _failure_info(fc: FailureClass) -> FailureInfo:
    p = profile_for(fc)
    return FailureInfo(
        classification=fc.value,
        retryable=p.retryable,
        correctable_by=p.correctable_by.value,
        recommended_action=p.recommended_action,
        alert_severity=p.alert_severity.value,
    )


def _clock() -> Callable[[], datetime]:
    return lambda: FIXED_NOW


def _valid_create(idempotency_key: str, **overrides: Any) -> CreateAccountRequest:
    base: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "account_name": "payments-prod",
        "owner": "team@example.com",
        "environment": "production",
        "data_classification": "confidential",
        "monthly_budget": 5000,
        "regions": ["ca-central-1"],
    }
    base.update(overrides)
    return CreateAccountRequest(**base)


# ── provisioning scenarios ──────────────────────────────────────────────────
def run_provisioning(scenario: str) -> ScenarioResult:
    if scenario not in PROVISIONING_SCENARIOS:
        raise ValueError(f"unknown provisioning scenario: {scenario}")

    svc = ControlPlaneService(
        InMemoryAccountRequestRepository(),
        config=ControlPlaneConfig(approval_ttl_minutes=60),
        clock=_clock(),
    )
    initial = RequestState.SUBMITTED.value
    failure: FailureInfo | None = None
    success = False
    retry_attempts = 0

    if scenario == "policy-rejected":
        req = svc.submit(
            _valid_create(
                "idem-sim-polrej", regions=["ap-southeast-2"], data_classification="public"
            )
        )
        failure = _failure_info(FailureClass.POLICY_DENIED)

    elif scenario == "approval-rejected":
        req = svc.submit(_valid_create("idem-sim-apprej"))
        req = svc.reject(req.request_id, approver="approver@example.com", reason="not needed")

    elif scenario == "approval-timeout":
        # Advance the clock relative to the request's own created_at so expiry is
        # deterministic regardless of wall-clock time.
        req = svc.submit(_valid_create("idem-sim-timeout"))
        past_ttl = req.created_at + timedelta(minutes=61)
        svc_future = ControlPlaneService(
            svc._repo, config=ControlPlaneConfig(approval_ttl_minutes=60), clock=lambda: past_ttl
        )
        req = svc_future.expire_if_stale(req.request_id)
        failure = _failure_info(FailureClass.APPROVAL_TIMEOUT)

    else:
        # success / retryable-baseline-failure / retry-exhaustion / manual-intervention
        req = svc.submit(_valid_create(f"idem-sim-{scenario}"))
        svc.approve(req.request_id, approver="approver@example.com")
        steps, expected_failure = _provisioning_steps(scenario)
        report = ProvisioningSimulator(svc, steps).run(req.request_id)
        retry_attempts = sum(s.attempts for s in report.steps)
        if report.terminal_state == RequestState.COMPLETED:
            success = True
        else:
            failure = _failure_info(expected_failure)
        req = svc.get(req.request_id)

    if scenario in ("success",):
        success = True

    audit = svc.audit_trail(req.request_id)
    return ScenarioResult(
        scenario=scenario,
        workflow_type="provisioning",
        request_id=req.request_id,
        correlation_id=req.request_id,  # provisioning uses request_id as correlation
        initial_state=initial,
        final_state=req.state.value,
        success=success,
        failure=failure,
        retry_attempts=retry_attempts,
        transitions=[{"sequence": e.sequence, "action": e.action.value} for e in audit],
        audit_chain_valid=verify_chain(audit),
    )


def _flaky_then_ok(succeed_on: int) -> Callable[[dict[str, Any]], None]:
    calls = {"n": 0}

    def task(ctx: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] < succeed_on:
            from platform_control_plane.exceptions import RetryableStepError

            raise RetryableStepError("throttled", step_name="ApplyBaseline")

    return task


def _provisioning_steps(scenario: str) -> tuple[list[WorkflowStep] | None, FailureClass]:
    from platform_control_plane.exceptions import RetryableStepError, WorkflowStepError

    def create(ctx: dict[str, Any]) -> None:
        ctx["aws_account_id"] = ctx.get("aws_account_id") or "111111111111"

    def ok(ctx: dict[str, Any]) -> None:
        return None

    if scenario == "success":
        return None, FailureClass.MANUAL_INTERVENTION_REQUIRED
    if scenario == "retryable-baseline-failure":
        return [
            WorkflowStep("CreateAccount", create, RetryPolicy(5), post_account_creation=False),
            WorkflowStep("ApplyBaseline", _flaky_then_ok(3), RetryPolicy(5)),
            WorkflowStep("AssignIdentity", ok, RetryPolicy(1)),
        ], FailureClass.RETRYABLE_DEPENDENCY_FAILURE
    if scenario == "retry-exhaustion":

        def always_throttle(ctx: dict[str, Any]) -> None:
            raise RetryableStepError("throttled", step_name="ApplyBaseline")

        return [
            WorkflowStep("CreateAccount", create, RetryPolicy(3), post_account_creation=False),
            WorkflowStep("ApplyBaseline", always_throttle, RetryPolicy(3)),
        ], FailureClass.PERMANENT_DEPENDENCY_FAILURE
    # manual-intervention: permanent baseline failure after account created.

    def denied(ctx: dict[str, Any]) -> None:
        raise WorkflowStepError("SCP denied Config", step_name="ApplyBaseline")

    return [
        WorkflowStep("CreateAccount", create, RetryPolicy(5), post_account_creation=False),
        WorkflowStep("ApplyBaseline", denied, RetryPolicy(3)),
    ], FailureClass.MANUAL_INTERVENTION_REQUIRED


# ── decommissioning scenarios ───────────────────────────────────────────────
def run_decommissioning(scenario: str) -> ScenarioResult:
    if scenario not in DECOMMISSIONING_SCENARIOS:
        raise ValueError(f"unknown decommissioning scenario: {scenario}")

    source = _decommission_source(scenario)
    svc = DecommissioningService(
        InMemoryDecommissioningRepository(),
        source,
        config=ControlPlaneConfig(quarantine_retention_days=7),
        clock=_clock(),
    )
    req = svc.submit(
        idempotency_key=f"idem-dec-{scenario}",
        aws_account_id="111111111111",
        requested_by="owner@example.com",
        reason="project sunset",
    )
    did = req.decommission_id
    initial = req.state.value
    failure: FailureInfo | None = None
    success = False

    if scenario == "unauthorized":
        try:
            svc.run_reviews(did, authorizer="owner@example.com")  # self-authorization
        except AuthorizationError:
            failure = _failure_info(FailureClass.AUTHORIZATION_ERROR)
    else:
        req = svc.run_reviews(did, authorizer="approver@example.com")
        if req.state.value == "DECOMMISSION_ACTION_REQUIRED":
            if scenario == "risk-accepted":
                req = svc.accept_risk(
                    did, accepted_by="director@example.com", justification="budget owner accepts"
                )
                req = svc.approve_and_quarantine(did, approver="approver@example.com")
                success = True
            else:
                failure = _failure_info(_require_fc(req))
        elif scenario == "evidence-export-failure":
            req = svc.approve_and_quarantine(
                did, approver="approver@example.com", evidence_ok=False
            )
            failure = _failure_info(_require_fc(req))
        elif scenario == "workload-disablement-failure":
            req = svc.approve_and_quarantine(
                did, approver="approver@example.com", workloads_ok=False
            )
            failure = _failure_info(_require_fc(req))
        else:
            req = svc.approve_and_quarantine(did, approver="approver@example.com")
            if scenario == "closure-success":
                closer = DecommissioningService(
                    svc._repo, source, clock=lambda: FIXED_NOW + timedelta(days=8)
                )
                req = closer.close_if_elapsed(did)
            success = True

    final = svc.get(did)
    audit = svc.audit_trail(did)
    return ScenarioResult(
        scenario=scenario,
        workflow_type="decommissioning",
        request_id=did,
        correlation_id=final.correlation_id,
        initial_state=initial,
        final_state=final.state.value,
        success=success,
        failure=failure,
        findings=[f.model_dump() for f in final.all_findings()],
        transitions=[{"sequence": e.sequence, "action": e.action.value} for e in audit],
        audit_chain_valid=verify_chain(audit),
    )


def _decommission_source(scenario: str) -> StaticDecommissionDataSource:
    end = FIXED_NOW + timedelta(days=180)
    if scenario == "active-dependency":
        return StaticDecommissionDataSource(
            retained_resources=[
                RetainedResource(
                    resource_id="rds-prod-01", resource_type=RetainedResourceType.RESIDUAL_COMPUTE
                )
            ]
        )
    if scenario == "active-ri":
        return StaticDecommissionDataSource(
            commitments=[
                Commitment(
                    commitment_id="ri-1",
                    commitment_type=CommitmentType.RESERVED_INSTANCE,
                    monthly_cost=800.0,
                    end_date=end,
                    cancellable=False,
                    service="Amazon RDS",
                )
            ]
        )
    if scenario == "active-savings-plan":
        return StaticDecommissionDataSource(
            commitments=[
                Commitment(
                    commitment_id="sp-1",
                    commitment_type=CommitmentType.SAVINGS_PLAN,
                    monthly_cost=300.0,
                    end_date=end,
                    cancellable=False,
                )
            ]
        )
    if scenario == "risk-accepted":
        return StaticDecommissionDataSource(
            commitments=[
                Commitment(
                    commitment_id="ri-1",
                    commitment_type=CommitmentType.RESERVED_INSTANCE,
                    monthly_cost=800.0,
                    end_date=end,
                    cancellable=False,
                )
            ]
        )
    if scenario == "residual-spend-block":
        return StaticDecommissionDataSource(daily_spend=[50.0, 40.0, 60.0])
    return StaticDecommissionDataSource(daily_spend=[0.0, 0.0, 0.0])


def run_scenario(workflow_type: str, scenario: str) -> ScenarioResult:
    """Dispatch entry point used by the CLI."""
    if workflow_type == "provisioning":
        return run_provisioning(scenario)
    if workflow_type == "decommissioning":
        return run_decommissioning(scenario)
    raise ValueError(f"unknown workflow type: {workflow_type}")


__all__ = [
    "DECOMMISSIONING_SCENARIOS",
    "PROVISIONING_SCENARIOS",
    "PlatformError",
    "ScenarioResult",
    "run_scenario",
]
