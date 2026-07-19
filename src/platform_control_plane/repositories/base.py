"""Repository interface — keeps the domain and services independent of boto3.

The service layer depends only on :class:`AbstractAccountRequestRepository`.
Concrete adapters (in-memory for local use and tests, DynamoDB for production)
implement this contract, so business logic never imports the AWS SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from platform_control_plane.domain.account_request import AccountRequest
    from platform_control_plane.domain.audit import AuditEvent


class AbstractAccountRequestRepository(ABC):
    """Persistence contract for account requests and their audit trail.

    Implementations MUST provide:

    - Idempotent creation: a second create with the same idempotency key is
      rejected via :class:`DuplicateRequestError` (see concrete adapters).
    - Optimistic concurrency: ``update`` takes the version the caller read and
      rejects the write via :class:`ConcurrencyError` if the stored version has
      advanced.
    """

    @abstractmethod
    def create(self, request: AccountRequest) -> None:
        """Persist a new request, enforcing idempotency on the idempotency key."""

    @abstractmethod
    def get(self, request_id: str) -> tuple[AccountRequest, int]:
        """Return the request and its current version, or raise AccountNotFoundError."""

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_key: str) -> AccountRequest | None:
        """Return the request that owns an idempotency key, or None."""

    @abstractmethod
    def update(self, request: AccountRequest, expected_version: int) -> None:
        """Persist a mutation using optimistic locking on ``expected_version``."""

    @abstractmethod
    def list_all(self) -> list[AccountRequest]:
        """Return all requests (for local inspection / CLI listing)."""

    @abstractmethod
    def append_audit(self, event: AuditEvent) -> None:
        """Append an immutable audit event."""

    @abstractmethod
    def get_audit(self, request_id: str) -> list[AuditEvent]:
        """Return the audit trail for a request in chronological order."""
