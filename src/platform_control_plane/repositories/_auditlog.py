"""Shared append-only audit-chain helper used by every adapter.

Centralizes the tamper-evidence rules so provisioning and decommissioning
repositories cannot diverge: monotonic sequence, previous-hash linkage, and
rejection of duplicate ``audit_id`` values within a request's chain.
"""

from __future__ import annotations

from platform_control_plane.domain.audit import GENESIS_HASH, AuditEvent, seal_event
from platform_control_plane.exceptions import AuditIntegrityError


def append_to_chain(chain: list[AuditEvent], event: AuditEvent) -> AuditEvent:
    """Seal ``event`` onto ``chain`` in place and return the sealed event.

    Raises :class:`AuditIntegrityError` if the event's ``audit_id`` already
    exists in the chain (an audit id must be unique per request).
    """
    if any(existing.audit_id == event.audit_id for existing in chain):
        raise AuditIntegrityError(
            "Duplicate audit_id in request chain",
            details={"audit_id": event.audit_id, "request_id": event.request_id},
        )
    prev_hash = chain[-1].event_hash if chain else GENESIS_HASH
    sealed = seal_event(event, sequence=len(chain) + 1, prev_hash=prev_hash)
    chain.append(sealed)
    return sealed
