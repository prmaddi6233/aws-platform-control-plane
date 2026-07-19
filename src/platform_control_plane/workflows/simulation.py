"""Deterministic in-process simulation of the provisioning workflow.

The Step Functions ASL (``statemachines/provisioning.asl.json``) cannot be
executed in CI without AWS. This simulator models the *same* control flow —
ordered tasks, per-task retry with bounded attempts, catch-to-failure, DLQ
routing, and the FAILED-vs-ACTION_REQUIRED distinction — so that failure and
recovery behaviour is testable and deterministic.

It is a test/validation harness, not production code. Real execution uses Step
Functions; see docs/production-boundaries.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from platform_control_plane.domain.account_request import RequestState
from platform_control_plane.exceptions import RetryableStepError, WorkflowStepError
from platform_control_plane.telemetry.logging import get_logger

if TYPE_CHECKING:
    from platform_control_plane.services.control_plane import ControlPlaneService

logger = get_logger(__name__)

# A task mutates the shared context (e.g. sets aws_account_id) and returns None,
# or raises RetryableStepError (transient) / WorkflowStepError (permanent).
Task = Callable[[dict[str, Any]], None]


@dataclass
class RetryPolicy:
    """Bounded retry for a single task. Backoff is counted, not slept."""

    max_attempts: int = 1


@dataclass
class WorkflowStep:
    name: str
    task: Task
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # Steps after the account exists: their permanent failure yields
    # ACTION_REQUIRED (account created, needs remediation) rather than FAILED.
    post_account_creation: bool = True


class StepResult(BaseModel):
    step: str
    attempts: int
    status: str  # SUCCESS | FAILED


class SimulationReport(BaseModel):
    request_id: str
    terminal_state: RequestState
    steps: list[StepResult]
    dlq: list[dict[str, Any]]
    failed_step: str | None = None
    error: str | None = None


# ── default (all-success) tasks ─────────────────────────────────────────────
def _create_account(ctx: dict[str, Any]) -> None:
    ctx["aws_account_id"] = ctx.get("aws_account_id") or "111111111111"


def _noop(ctx: dict[str, Any]) -> None:  # baseline / identity / budget / notify happy path
    return None


def default_steps() -> list[WorkflowStep]:
    """The provisioning task sequence mirroring the ASL, all succeeding."""
    return [
        WorkflowStep("CreateAccount", _create_account, RetryPolicy(5), post_account_creation=False),
        WorkflowStep("ApplyBaseline", _noop, RetryPolicy(3)),
        WorkflowStep("AssignIdentity", _noop, RetryPolicy(1)),
        WorkflowStep("CreateBudget", _noop, RetryPolicy(3)),
        WorkflowStep("NotifyOwner", _noop, RetryPolicy(2)),
    ]


class ProvisioningSimulator:
    """Runs the provisioning steps against a real ControlPlaneService."""

    def __init__(
        self, service: ControlPlaneService, steps: list[WorkflowStep] | None = None
    ) -> None:
        self._service = service
        self._steps = steps if steps is not None else default_steps()

    def run(self, request_id: str, *, execution_id: str = "sim-exec") -> SimulationReport:
        request = self._service.get(request_id)

        # Idempotent (re)entry: only start provisioning if not already running.
        if request.state != RequestState.PROVISIONING:
            request = self._service.start_provisioning(request_id, execution_id)

        ctx: dict[str, Any] = {
            "request_id": request_id,
            "account_name": request.account_name,
            # Recovery-after-interruption: if the account already exists, carry it.
            "aws_account_id": request.aws_account_id,
        }
        results: list[StepResult] = []
        dlq: list[dict[str, Any]] = []

        for step in self._steps:
            # Skip account creation on resume if the account already exists.
            if step.name == "CreateAccount" and ctx.get("aws_account_id"):
                results.append(StepResult(step=step.name, attempts=0, status="SUCCESS"))
                continue

            attempts, error = self._run_step(step, ctx)
            if error is None:
                # Persist the account id as soon as it exists, so an interrupted
                # run can resume without re-creating the account.
                if step.name == "CreateAccount" and ctx.get("aws_account_id"):
                    self._service.record_account_created(request_id, ctx["aws_account_id"])
                results.append(StepResult(step=step.name, attempts=attempts, status="SUCCESS"))
                continue

            # Task failed permanently or exhausted retries → catch path.
            results.append(StepResult(step=step.name, attempts=attempts, status="FAILED"))
            dlq.append({"step": step.name, "request_id": request_id, "error": error})

            if ctx.get("aws_account_id") and step.post_account_creation:
                self._service.require_action(
                    request_id, step=step.name, reason=error, aws_account_id=ctx["aws_account_id"]
                )
                terminal = RequestState.ACTION_REQUIRED
            else:
                self._service.fail_provisioning(request_id, step=step.name, error=error)
                terminal = RequestState.FAILED

            return SimulationReport(
                request_id=request_id,
                terminal_state=terminal,
                steps=results,
                dlq=dlq,
                failed_step=step.name,
                error=error,
            )

        self._service.complete_provisioning(request_id, ctx["aws_account_id"])
        return SimulationReport(
            request_id=request_id,
            terminal_state=RequestState.COMPLETED,
            steps=results,
            dlq=dlq,
        )

    def _run_step(self, step: WorkflowStep, ctx: dict[str, Any]) -> tuple[int, str | None]:
        """Execute a task with bounded retries. Returns (attempts, error|None)."""
        last_error: str | None = None
        for attempt in range(1, step.retry.max_attempts + 1):
            try:
                step.task(ctx)
                return attempt, None
            except RetryableStepError as exc:
                last_error = str(exc)
                logger.warning(
                    "workflow_step_retry", step=step.name, attempt=attempt, error=last_error
                )
                continue  # backoff would apply here in the real workflow
            except WorkflowStepError as exc:
                # Non-retryable: fail immediately.
                return attempt, str(exc)
        # Retries exhausted.
        return step.retry.max_attempts, last_error or "retry attempts exhausted"
