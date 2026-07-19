"""In-memory account request repository.

Serves two purposes:

1. A deterministic test double for behavioural tests (idempotency, optimistic
   concurrency, audit chaining) with no AWS dependency.
2. The backing store for the local CLI. An optional ``persist_path`` snapshots
   state to JSON after every write so ``submit`` and ``status`` work across
   separate process invocations without needing DynamoDB.

The file store is written **atomically** (temp file + ``os.replace``) with a
``_store_version`` header. A corrupt or unversioned file raises rather than being
silently discarded. This adapter is for local demonstration, NOT production
concurrency — cross-process writes are last-writer-wins.
"""

from __future__ import annotations

import threading
from pathlib import Path

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.domain.audit import AuditEvent
from platform_control_plane.exceptions import (
    AccountNotFoundError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories._auditlog import append_to_chain
from platform_control_plane.repositories._filestore import (
    STORE_VERSION,
    atomic_write_json,
    read_versioned_json,
)
from platform_control_plane.repositories.base import AbstractAccountRequestRepository


class InMemoryAccountRequestRepository(AbstractAccountRequestRepository):
    """Thread-safe in-memory repository with optional atomic JSON file persistence."""

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._requests: dict[str, tuple[AccountRequest, int]] = {}
        self._idempotency: dict[str, str] = {}  # key -> request_id
        self._audit: dict[str, list[AuditEvent]] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path and self._persist_path.exists():
            self._load()

    # ── persistence helpers ────────────────────────────────────────────────
    def _load(self) -> None:
        assert self._persist_path is not None
        raw = read_versioned_json(self._persist_path)
        for rid, (data, ver) in raw.get("requests", {}).items():
            self._requests[rid] = (AccountRequest(**data), ver)
        self._idempotency = raw.get("idempotency", {})
        for rid, events in raw.get("audit", {}).items():
            self._audit[rid] = [AuditEvent(**e) for e in events]

    def _save(self) -> None:
        if not self._persist_path:
            return
        snapshot = {
            "_store_version": STORE_VERSION,
            "requests": {
                rid: [req.model_dump(mode="json"), version]
                for rid, (req, version) in self._requests.items()
            },
            "idempotency": self._idempotency,
            "audit": {
                rid: [e.model_dump(mode="json") for e in events]
                for rid, events in self._audit.items()
            },
        }
        atomic_write_json(self._persist_path, snapshot)

    # ── repository contract ────────────────────────────────────────────────
    def create(self, request: AccountRequest) -> None:
        with self._lock:
            if request.idempotency_key in self._idempotency:
                raise DuplicateRequestError(
                    "Duplicate account request detected",
                    idempotency_key=request.idempotency_key,
                    existing_request_id=self._idempotency[request.idempotency_key],
                )
            if request.request_id in self._requests:
                raise DuplicateRequestError(
                    "Request id already exists",
                    idempotency_key=request.idempotency_key,
                    existing_request_id=request.request_id,
                )
            self._requests[request.request_id] = (request.model_copy(deep=True), 1)
            self._idempotency[request.idempotency_key] = request.request_id
            self._save()

    def get(self, request_id: str) -> tuple[AccountRequest, int]:
        with self._lock:
            entry = self._requests.get(request_id)
            if entry is None:
                raise AccountNotFoundError(f"Account request {request_id} not found")
            request, version = entry
            return request.model_copy(deep=True), version

    def get_by_idempotency_key(self, idempotency_key: str) -> AccountRequest | None:
        with self._lock:
            request_id = self._idempotency.get(idempotency_key)
            if request_id is None:
                return None
            return self._requests[request_id][0].model_copy(deep=True)

    def update(self, request: AccountRequest, expected_version: int) -> None:
        with self._lock:
            entry = self._requests.get(request.request_id)
            if entry is None:
                raise AccountNotFoundError(f"Account request {request.request_id} not found")
            _, current_version = entry
            if current_version != expected_version:
                raise ConcurrencyError(
                    f"Optimistic lock failed for request {request.request_id}: "
                    f"expected v{expected_version}, found v{current_version}"
                )
            self._requests[request.request_id] = (
                request.model_copy(deep=True),
                current_version + 1,
            )
            self._save()

    def list_all(self) -> list[AccountRequest]:
        with self._lock:
            return [req.model_copy(deep=True) for req, _ in self._requests.values()]

    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            chain = self._audit.setdefault(event.request_id, [])
            append_to_chain(chain, event)
            self._save()

    def get_audit(self, request_id: str) -> list[AuditEvent]:
        with self._lock:
            return sorted(self._audit.get(request_id, []), key=lambda e: e.sequence)
