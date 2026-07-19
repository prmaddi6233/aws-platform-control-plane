"""Audit event model — an append-only, tamper-evident record.

Every state change, policy evaluation, and approval action produces one audit
event. Events are:

- **Frozen** (Pydantic ``frozen=True``) — no field can be mutated after creation.
- **Sequenced** — a monotonic per-request sequence number; gaps or reordering
  are detectable.
- **Hash-chained** — each event carries the hash of the previous event and its
  own hash over its canonical content, so any insertion, deletion, reordering,
  or field edit breaks the chain.

This makes the trail **tamper-evident**, not tamper-proof: a party who can
rewrite the entire store could recompute the chain. Detection, not prevention,
is the guarantee. Repositories enforce append-only semantics (no update/delete)
and reject duplicate ``audit_id`` / sequence values.
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

GENESIS_HASH = "0" * 64


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _audit_id() -> str:
    return f"aud-{uuid.uuid4().hex[:12]}"


class AuditAction(enum.StrEnum):
    """The category of action captured by an audit event."""

    REQUEST_SUBMITTED = "REQUEST_SUBMITTED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    POLICY_REJECTED = "POLICY_REJECTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    STATE_CHANGED = "STATE_CHANGED"
    PROVISIONING_STARTED = "PROVISIONING_STARTED"
    PROVISIONING_COMPLETED = "PROVISIONING_COMPLETED"
    PROVISIONING_FAILED = "PROVISIONING_FAILED"
    DECOMMISSION_REQUESTED = "DECOMMISSION_REQUESTED"
    DECOMMISSION_STATE_CHANGED = "DECOMMISSION_STATE_CHANGED"
    DECOMMISSION_BLOCKED = "DECOMMISSION_BLOCKED"
    DECOMMISSION_CLOSED = "DECOMMISSION_CLOSED"


class AuditEvent(BaseModel):
    """An immutable, hash-chained audit record tied to a single request."""

    model_config = {"frozen": True}

    audit_id: str = Field(default_factory=_audit_id)
    request_id: str
    action: AuditAction
    actor: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    detail: dict[str, Any] = Field(default_factory=dict)

    # Tamper-evidence fields (assigned by the repository when appended).
    sequence: int = 0
    correlation_id: str = ""
    request_version: int | None = None
    prev_hash: str = GENESIS_HASH
    event_hash: str = ""

    def digest(self) -> str:
        """Compute this event's hash over its canonical content + prev_hash."""
        payload = {
            "audit_id": self.audit_id,
            "request_id": self.request_id,
            "action": self.action.value,
            "actor": self.actor,
            "occurred_at": self.occurred_at.isoformat(),
            "detail": self.detail,
            "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "request_version": self.request_version,
            "prev_hash": self.prev_hash,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()


def seal_event(event: AuditEvent, *, sequence: int, prev_hash: str) -> AuditEvent:
    """Return a sealed copy of ``event`` with its chain position and hash set."""
    positioned = event.model_copy(update={"sequence": sequence, "prev_hash": prev_hash})
    return positioned.model_copy(update={"event_hash": positioned.digest()})


def verify_chain(events: list[AuditEvent]) -> bool:
    """Return True iff the events form an intact, contiguous hash chain.

    Checks: sequences are 1..n contiguous, each ``prev_hash`` matches the prior
    event's ``event_hash`` (genesis for the first), and every ``event_hash``
    recomputes from the event's content.
    """
    prev = GENESIS_HASH
    for index, event in enumerate(events, start=1):
        if event.sequence != index:
            return False
        if event.prev_hash != prev:
            return False
        if event.event_hash != event.digest():
            return False
        prev = event.event_hash
    return True
