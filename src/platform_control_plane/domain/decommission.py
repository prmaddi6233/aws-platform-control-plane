"""Decommissioning domain: a first-class workflow with an enforced state machine.

Closing an AWS account is high-consequence and effectively irreversible, so the
lifecycle is modelled explicitly:

- a rich set of states with an **explicit transition map** (arbitrary jumps are
  rejected),
- typed **findings** for dependency, commitment, and residual-spend gates,
- deterministic gate functions (pure, no I/O, no wall clock),
- risk acceptance to progress past a blocking finding with an audit record.

All monetary figures are computed from the supplied synthetic inputs. Nothing
here is connected to live AWS billing; `services/decommissioning.py` defines the
data-source interface a real Cost Explorer / CUR-FOCUS / Organizations
integration would implement.
"""

from __future__ import annotations

import enum
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from platform_control_plane.domain.failures import FailureClass
from platform_control_plane.exceptions import AuthorizationError, StateTransitionError


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── states & transition map ─────────────────────────────────────────────────
class DecommissionState(enum.StrEnum):
    DECOMMISSION_REQUESTED = "DECOMMISSION_REQUESTED"
    DECOMMISSION_VALIDATING = "DECOMMISSION_VALIDATING"
    DISCOVERING_DEPENDENCIES = "DISCOVERING_DEPENDENCIES"
    COMMITMENT_REVIEW = "COMMITMENT_REVIEW"
    RESIDUAL_SPEND_REVIEW = "RESIDUAL_SPEND_REVIEW"
    AWAITING_DECOMMISSION_APPROVAL = "AWAITING_DECOMMISSION_APPROVAL"
    EXPORTING_EVIDENCE = "EXPORTING_EVIDENCE"
    DISABLING_WORKLOADS = "DISABLING_WORKLOADS"
    QUARANTINED = "QUARANTINED"
    RETENTION_PERIOD = "RETENTION_PERIOD"
    READY_FOR_CLOSURE = "READY_FOR_CLOSURE"
    CLOSING_ACCOUNT = "CLOSING_ACCOUNT"
    CLOSED = "CLOSED"
    DECOMMISSION_ACTION_REQUIRED = "DECOMMISSION_ACTION_REQUIRED"
    DECOMMISSION_FAILED = "DECOMMISSION_FAILED"
    DECOMMISSION_CANCELLED = "DECOMMISSION_CANCELLED"


_S = DecommissionState
TERMINAL_STATES = {_S.CLOSED, _S.DECOMMISSION_FAILED, _S.DECOMMISSION_CANCELLED}

# Explicit allowed transitions. FAILED/CANCELLED are reachable from any
# non-terminal state and are added programmatically below.
_ALLOWED: dict[DecommissionState, set[DecommissionState]] = {
    _S.DECOMMISSION_REQUESTED: {_S.DECOMMISSION_VALIDATING},
    _S.DECOMMISSION_VALIDATING: {_S.DISCOVERING_DEPENDENCIES},
    _S.DISCOVERING_DEPENDENCIES: {_S.COMMITMENT_REVIEW, _S.DECOMMISSION_ACTION_REQUIRED},
    _S.COMMITMENT_REVIEW: {_S.RESIDUAL_SPEND_REVIEW, _S.DECOMMISSION_ACTION_REQUIRED},
    _S.RESIDUAL_SPEND_REVIEW: {
        _S.AWAITING_DECOMMISSION_APPROVAL,
        _S.DECOMMISSION_ACTION_REQUIRED,
    },
    _S.AWAITING_DECOMMISSION_APPROVAL: {_S.EXPORTING_EVIDENCE},
    _S.EXPORTING_EVIDENCE: {_S.DISABLING_WORKLOADS},
    _S.DISABLING_WORKLOADS: {_S.QUARANTINED},
    _S.QUARANTINED: {_S.RETENTION_PERIOD},
    _S.RETENTION_PERIOD: {_S.READY_FOR_CLOSURE},
    _S.READY_FOR_CLOSURE: {_S.CLOSING_ACCOUNT},
    _S.CLOSING_ACCOUNT: {_S.CLOSED},
    # After remediation or explicit risk acceptance, a blocked request re-drives.
    _S.DECOMMISSION_ACTION_REQUIRED: {_S.AWAITING_DECOMMISSION_APPROVAL},
    _S.CLOSED: set(),
    _S.DECOMMISSION_FAILED: set(),
    _S.DECOMMISSION_CANCELLED: set(),
}
for _state in _S:
    if _state not in TERMINAL_STATES:
        _ALLOWED[_state] |= {_S.DECOMMISSION_FAILED, _S.DECOMMISSION_CANCELLED}


def allowed_transitions(state: DecommissionState) -> set[DecommissionState]:
    """Return the states reachable from ``state`` (for docs/contract tests)."""
    return set(_ALLOWED[state])


# ── findings & records ──────────────────────────────────────────────────────
class FindingSeverity(enum.StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class DecommissionFinding(BaseModel):
    """A typed pre-closure finding. Subtypes set ``finding_type`` values."""

    model_config = {"frozen": True}

    finding_type: str
    severity: FindingSeverity
    resource_or_commitment_id: str
    estimated_remaining_cost: float = 0.0
    currency: str = "USD"
    reason: str
    required_action: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class DependencyFinding(DecommissionFinding):
    """Active/retained resource that would remain after closure."""


class CommitmentFinding(DecommissionFinding):
    """Active RI / Savings Plan / Marketplace commitment attributed to the account."""


class ResidualSpendFinding(DecommissionFinding):
    """Post-shutdown spend above the closure threshold."""


class EvidenceExport(BaseModel):
    model_config = {"frozen": True}
    export_id: str = Field(default_factory=lambda: _new_id("evd"))
    location: str
    sha256: str
    item_count: int
    exported_at: datetime = Field(default_factory=_utcnow)


class QuarantineRecord(BaseModel):
    model_config = {"frozen": True}
    entered_at: datetime
    quarantine_until: datetime


class ClosureDecision(BaseModel):
    model_config = {"frozen": True}
    decided_by: str
    approved: bool
    decided_at: datetime = Field(default_factory=_utcnow)
    notes: str = ""


class RiskAcceptance(BaseModel):
    model_config = {"frozen": True}
    accepted_by: str
    justification: str
    accepted_at: datetime = Field(default_factory=_utcnow)


# ── deterministic gate inputs ───────────────────────────────────────────────
class CommitmentType(enum.StrEnum):
    RESERVED_INSTANCE = "RESERVED_INSTANCE"
    SAVINGS_PLAN = "SAVINGS_PLAN"
    MARKETPLACE = "MARKETPLACE"


class Commitment(BaseModel):
    model_config = {"frozen": True}
    commitment_id: str
    commitment_type: CommitmentType
    service: str = ""
    monthly_cost: float = Field(ge=0)
    end_date: datetime
    cancellable: bool = False


class RetainedResourceType(enum.StrEnum):
    SNAPSHOT = "SNAPSHOT"
    PERSISTENT_STORAGE = "PERSISTENT_STORAGE"
    RESIDUAL_COMPUTE = "RESIDUAL_COMPUTE"
    SHARED_DEPENDENCY = "SHARED_DEPENDENCY"


class RetainedResource(BaseModel):
    model_config = {"frozen": True}
    resource_id: str
    resource_type: RetainedResourceType
    monthly_cost: float = Field(default=0.0, ge=0)
    service: str = ""


# ── deterministic gate functions ────────────────────────────────────────────
_BLOCKING_RESOURCE_TYPES = {
    RetainedResourceType.PERSISTENT_STORAGE,
    RetainedResourceType.RESIDUAL_COMPUTE,
    RetainedResourceType.SHARED_DEPENDENCY,
}


def review_commitments(
    commitments: list[Commitment], *, now: datetime | None = None
) -> list[CommitmentFinding]:
    """Flag non-cancellable commitments whose term outlives the account.

    ``estimated_remaining_cost`` = ceil(remaining_days / 30) * monthly_cost, so
    the figure is traceable to the supplied inputs (no external pricing).
    """
    reference = now or _utcnow()
    findings: list[CommitmentFinding] = []
    for c in commitments:
        if c.cancellable or c.end_date <= reference:
            continue
        remaining_months = max(1, math.ceil((c.end_date - reference).days / 30))
        findings.append(
            CommitmentFinding(
                finding_type=f"ACTIVE_{c.commitment_type.value}",
                severity=FindingSeverity.BLOCKING,
                resource_or_commitment_id=c.commitment_id,
                estimated_remaining_cost=round(c.monthly_cost * remaining_months, 2),
                reason="The commitment remains active after workload shutdown.",
                required_action="Reassign, modify or explicitly accept the remaining cost.",
                evidence={
                    "service": c.service,
                    "monthly_cost": c.monthly_cost,
                    "remaining_months": remaining_months,
                    "expiration_date": c.end_date.date().isoformat(),
                },
            )
        )
    return findings


def discover_dependencies(resources: list[RetainedResource]) -> list[DependencyFinding]:
    """Classify retained resources; storage/compute/shared block, snapshots warn."""
    findings: list[DependencyFinding] = []
    for r in resources:
        blocking = r.resource_type in _BLOCKING_RESOURCE_TYPES
        findings.append(
            DependencyFinding(
                finding_type=f"RETAINED_{r.resource_type.value}",
                severity=FindingSeverity.BLOCKING if blocking else FindingSeverity.WARNING,
                resource_or_commitment_id=r.resource_id,
                estimated_remaining_cost=round(r.monthly_cost, 2),
                reason="Resource would remain active/billing after account closure.",
                required_action=(
                    "Delete or migrate the resource before closure."
                    if blocking
                    else "Confirm the retained resource is intended."
                ),
                evidence={"service": r.service, "resource_type": r.resource_type.value},
            )
        )
    return findings


def check_residual_spend(
    daily_spend: list[float],
    *,
    lookback_days: int = 3,
    threshold_daily: float = 1.0,
) -> ResidualSpendFinding | None:
    """Detect residual spend from the trailing ``lookback_days`` window."""
    window = daily_spend[-lookback_days:] if daily_spend else []
    if not window:
        return None
    avg = round(sum(window) / len(window), 2)
    if avg <= threshold_daily:
        return None
    return ResidualSpendFinding(
        finding_type="RESIDUAL_SPEND",
        severity=FindingSeverity.BLOCKING,
        resource_or_commitment_id="account-spend",
        estimated_remaining_cost=round(avg * 30, 2),
        reason=(
            f"Residual spend ${avg:.2f}/day over the last {len(window)} day(s) "
            f"exceeds the ${threshold_daily:.2f} closure threshold."
        ),
        required_action="Disable remaining workloads until spend falls below threshold.",
        evidence={"observed_daily_spend": window, "threshold_daily": threshold_daily},
    )


# ── aggregate ───────────────────────────────────────────────────────────────
class DecommissioningRequest(BaseModel):
    """Aggregate root for an account decommissioning with an enforced FSM."""

    decommission_id: str = Field(default_factory=lambda: _new_id("dec"))
    idempotency_key: str
    aws_account_id: str
    requested_by: str
    reason: str = ""
    correlation_id: str = Field(default_factory=lambda: _new_id("corr"))

    state: DecommissionState = DecommissionState.DECOMMISSION_REQUESTED
    authorizer: str | None = None

    dependency_findings: list[DependencyFinding] = Field(default_factory=list)
    commitment_findings: list[CommitmentFinding] = Field(default_factory=list)
    residual_finding: ResidualSpendFinding | None = None
    risk_acceptance: RiskAcceptance | None = None

    evidence_export: EvidenceExport | None = None
    quarantine: QuarantineRecord | None = None
    closure_decision: ClosureDecision | None = None

    blocking_reasons: list[str] = Field(default_factory=list)
    failure_class: FailureClass | None = None
    failure_reason: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    model_config = {"extra": "ignore"}

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    # -- transition primitive --
    def _apply(self, target: DecommissionState) -> None:
        if target not in _ALLOWED[self.state]:
            raise StateTransitionError(
                f"Illegal decommission transition {self.state} -> {target}",
                current_state=self.state,
                attempted_state=target,
            )
        self.history.append(
            {"from": self.state.value, "to": target.value, "at": _utcnow().isoformat()}
        )
        self.state = target
        self.updated_at = _utcnow()

    # -- gate evaluation (query) --
    def all_findings(self) -> list[DecommissionFinding]:
        found: list[DecommissionFinding] = [*self.dependency_findings, *self.commitment_findings]
        if self.residual_finding is not None:
            found.append(self.residual_finding)
        return found

    def blocking_findings(self) -> list[DecommissionFinding]:
        return [f for f in self.all_findings() if f.severity == FindingSeverity.BLOCKING]

    def is_blocked(self) -> bool:
        return bool(self.blocking_findings()) and self.risk_acceptance is None

    # -- lifecycle methods --
    def begin_validation(self, authorizer: str) -> None:
        if authorizer == self.requested_by:
            raise AuthorizationError(
                "Authorizer must differ from requester (separation of duties).",
                details={"decommission_id": self.decommission_id},
            )
        self.authorizer = authorizer
        self._apply(_S.DECOMMISSION_VALIDATING)

    def record_dependencies(self, findings: list[DependencyFinding]) -> None:
        self._apply(_S.DISCOVERING_DEPENDENCIES)
        self.dependency_findings = findings

    def record_commitments(self, findings: list[CommitmentFinding]) -> None:
        self._apply(_S.COMMITMENT_REVIEW)
        self.commitment_findings = findings

    def record_residual(self, finding: ResidualSpendFinding | None) -> None:
        self._apply(_S.RESIDUAL_SPEND_REVIEW)
        self.residual_finding = finding

    def block(self) -> None:
        reasons = [f.reason for f in self.blocking_findings()]
        self._apply(_S.DECOMMISSION_ACTION_REQUIRED)
        self.blocking_reasons = reasons
        # Residual spend is its own class; commitment/dependency blocks share
        # COMMITMENT_BLOCK (both are user-correctable financial/resource holds).
        if (
            self.residual_finding is not None
            and self.residual_finding.severity == FindingSeverity.BLOCKING
        ):
            self.failure_class = FailureClass.RESIDUAL_SPEND_BLOCK
        else:
            self.failure_class = FailureClass.COMMITMENT_BLOCK

    def accept_risk(self, accepted_by: str, justification: str) -> None:
        if self.state not in (_S.DECOMMISSION_ACTION_REQUIRED, _S.RESIDUAL_SPEND_REVIEW):
            raise StateTransitionError(
                "Risk can only be accepted at a review or action-required gate.",
                current_state=self.state,
                attempted_state=self.state,
            )
        self.risk_acceptance = RiskAcceptance(accepted_by=accepted_by, justification=justification)
        self.updated_at = _utcnow()

    def request_approval(self) -> None:
        if self.is_blocked():
            raise StateTransitionError(
                "Cannot request approval while blocking findings are unresolved.",
                current_state=self.state,
                attempted_state=_S.AWAITING_DECOMMISSION_APPROVAL,
            )
        self._apply(_S.AWAITING_DECOMMISSION_APPROVAL)

    def approve_closure(self, approver: str) -> None:
        if approver == self.requested_by:
            raise AuthorizationError(
                "Closure approver must differ from requester (separation of duties).",
                details={"decommission_id": self.decommission_id},
            )
        self._apply(_S.EXPORTING_EVIDENCE)
        self.closure_decision = ClosureDecision(decided_by=approver, approved=True)

    def record_evidence(self, export: EvidenceExport) -> None:
        self._apply(_S.DISABLING_WORKLOADS)
        self.evidence_export = export

    def disable_workloads(self) -> None:
        self._apply(_S.QUARANTINED)

    def enter_quarantine(self, until: datetime, *, now: datetime | None = None) -> None:
        entered = now or _utcnow()
        self.quarantine = QuarantineRecord(entered_at=entered, quarantine_until=until)
        self._apply(_S.RETENTION_PERIOD)

    def mark_ready_for_closure(self, *, now: datetime | None = None) -> None:
        reference = now or _utcnow()
        if self.quarantine is not None and reference < self.quarantine.quarantine_until:
            raise StateTransitionError(
                "Quarantine retention period has not elapsed.",
                current_state=self.state,
                attempted_state=_S.READY_FOR_CLOSURE,
                details={"quarantine_until": self.quarantine.quarantine_until.isoformat()},
            )
        self._apply(_S.READY_FOR_CLOSURE)

    def begin_closure(self) -> None:
        self._apply(_S.CLOSING_ACCOUNT)

    def complete_closure(self) -> None:
        self._apply(_S.CLOSED)

    def fail(self, reason: str, *, failure_class: FailureClass) -> None:
        self._apply(_S.DECOMMISSION_FAILED)
        self.failure_reason = reason
        self.failure_class = failure_class

    def cancel(self, reason: str) -> None:
        self._apply(_S.DECOMMISSION_CANCELLED)
        self.failure_reason = reason
