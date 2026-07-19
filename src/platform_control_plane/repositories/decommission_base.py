"""Repository interface for the decommissioning aggregate.

Deliberately separate from :class:`AbstractAccountRequestRepository` (see
ADR 005): the aggregates have different invariants (a decommission is tied to an
account and enforces one active decommission per account). Concrete adapters
reuse the shared audit-chain and file-store infrastructure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from platform_control_plane.domain.audit import AuditEvent
    from platform_control_plane.domain.decommission import DecommissioningRequest


class AbstractDecommissioningRequestRepository(ABC):
    """Persistence contract for decommissioning requests and their audit trail."""

    @abstractmethod
    def create(self, request: DecommissioningRequest) -> None:
        """Persist a new request.

        Enforces idempotency on ``idempotency_key`` (``DuplicateRequestError``)
        and one active decommission per account
        (``ActiveDecommissionExistsError``).
        """

    @abstractmethod
    def get(self, decommission_id: str) -> tuple[DecommissioningRequest, int]:
        """Return the request and its version, or raise ``AccountNotFoundError``."""

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_key: str) -> DecommissioningRequest | None:
        """Return the request that owns an idempotency key, or ``None``."""

    @abstractmethod
    def get_active_for_account(self, aws_account_id: str) -> DecommissioningRequest | None:
        """Return the non-terminal decommission for an account, if any."""

    @abstractmethod
    def update(self, request: DecommissioningRequest, expected_version: int) -> None:
        """Persist a mutation using optimistic locking on ``expected_version``."""

    @abstractmethod
    def list_all(self) -> list[DecommissioningRequest]:
        """Return all decommissioning requests."""

    @abstractmethod
    def append_audit(self, event: AuditEvent) -> None:
        """Append an immutable, hash-chained audit event."""

    @abstractmethod
    def get_audit(self, decommission_id: str) -> list[AuditEvent]:
        """Return the audit trail ordered by sequence."""
