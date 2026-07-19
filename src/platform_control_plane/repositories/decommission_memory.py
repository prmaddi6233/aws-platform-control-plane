"""In-memory / file-backed decommissioning repository.

Mirrors the account-request in-memory adapter and reuses the same shared
infrastructure (atomic file store, audit hash-chain). For local demo and tests;
not for production concurrency.
"""

from __future__ import annotations

import threading
from pathlib import Path

from platform_control_plane.domain.audit import AuditEvent
from platform_control_plane.domain.decommission import (
    TERMINAL_STATES,
    DecommissioningRequest,
)
from platform_control_plane.exceptions import (
    AccountNotFoundError,
    ActiveDecommissionExistsError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories._auditlog import append_to_chain
from platform_control_plane.repositories._filestore import (
    STORE_VERSION,
    atomic_write_json,
    read_versioned_json,
)
from platform_control_plane.repositories.decommission_base import (
    AbstractDecommissioningRequestRepository,
)


class InMemoryDecommissioningRepository(AbstractDecommissioningRequestRepository):
    """Thread-safe in-memory decommissioning repository with optional file store."""

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._requests: dict[str, tuple[DecommissioningRequest, int]] = {}
        self._idempotency: dict[str, str] = {}  # key -> decommission_id
        self._audit: dict[str, list[AuditEvent]] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path and self._persist_path.exists():
            self._load()

    # ── persistence helpers ────────────────────────────────────────────────
    def _load(self) -> None:
        assert self._persist_path is not None
        raw = read_versioned_json(self._persist_path)
        for did, (data, ver) in raw.get("requests", {}).items():
            self._requests[did] = (DecommissioningRequest(**data), ver)
        self._idempotency = raw.get("idempotency", {})
        for did, events in raw.get("audit", {}).items():
            self._audit[did] = [AuditEvent(**e) for e in events]

    def _save(self) -> None:
        if not self._persist_path:
            return
        snapshot = {
            "_store_version": STORE_VERSION,
            "requests": {
                did: [req.model_dump(mode="json"), version]
                for did, (req, version) in self._requests.items()
            },
            "idempotency": self._idempotency,
            "audit": {
                did: [e.model_dump(mode="json") for e in events]
                for did, events in self._audit.items()
            },
        }
        atomic_write_json(self._persist_path, snapshot)

    # ── contract ───────────────────────────────────────────────────────────
    def create(self, request: DecommissioningRequest) -> None:
        with self._lock:
            if request.idempotency_key in self._idempotency:
                raise DuplicateRequestError(
                    "Duplicate decommissioning request detected",
                    idempotency_key=request.idempotency_key,
                    existing_request_id=self._idempotency[request.idempotency_key],
                )
            active = self._active_for_account(request.aws_account_id)
            if active is not None:
                raise ActiveDecommissionExistsError(
                    "An active decommissioning request already exists for this account",
                    aws_account_id=request.aws_account_id,
                    existing_decommission_id=active.decommission_id,
                )
            self._requests[request.decommission_id] = (request.model_copy(deep=True), 1)
            self._idempotency[request.idempotency_key] = request.decommission_id
            self._save()

    def get(self, decommission_id: str) -> tuple[DecommissioningRequest, int]:
        with self._lock:
            entry = self._requests.get(decommission_id)
            if entry is None:
                raise AccountNotFoundError(f"Decommission {decommission_id} not found")
            request, version = entry
            return request.model_copy(deep=True), version

    def get_by_idempotency_key(self, idempotency_key: str) -> DecommissioningRequest | None:
        with self._lock:
            did = self._idempotency.get(idempotency_key)
            if did is None:
                return None
            return self._requests[did][0].model_copy(deep=True)

    def get_active_for_account(self, aws_account_id: str) -> DecommissioningRequest | None:
        with self._lock:
            active = self._active_for_account(aws_account_id)
            return active.model_copy(deep=True) if active is not None else None

    def _active_for_account(self, aws_account_id: str) -> DecommissioningRequest | None:
        for req, _ in self._requests.values():
            if req.aws_account_id == aws_account_id and req.state not in TERMINAL_STATES:
                return req
        return None

    def update(self, request: DecommissioningRequest, expected_version: int) -> None:
        with self._lock:
            entry = self._requests.get(request.decommission_id)
            if entry is None:
                raise AccountNotFoundError(f"Decommission {request.decommission_id} not found")
            _, current_version = entry
            if current_version != expected_version:
                raise ConcurrencyError(
                    f"Optimistic lock failed for decommission {request.decommission_id}: "
                    f"expected v{expected_version}, found v{current_version}"
                )
            self._requests[request.decommission_id] = (
                request.model_copy(deep=True),
                current_version + 1,
            )
            self._save()

    def list_all(self) -> list[DecommissioningRequest]:
        with self._lock:
            return [req.model_copy(deep=True) for req, _ in self._requests.values()]

    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            chain = self._audit.setdefault(event.request_id, [])
            append_to_chain(chain, event)
            self._save()

    def get_audit(self, decommission_id: str) -> list[AuditEvent]:
        with self._lock:
            return sorted(self._audit.get(decommission_id, []), key=lambda e: e.sequence)
