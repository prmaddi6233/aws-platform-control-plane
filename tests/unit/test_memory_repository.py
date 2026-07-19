"""Behavioural tests for the in-memory repository adapter."""

import pytest

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.domain.audit import AuditAction, AuditEvent
from platform_control_plane.exceptions import (
    AccountNotFoundError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository


def _request(idempotency_key: str = "idem-key-mem-0001") -> AccountRequest:
    return AccountRequest(
        idempotency_key=idempotency_key,
        account_name="payments-prod",
        owner="team@example.com",
        environment="production",
        data_classification="confidential",
        monthly_budget=5000,
        regions=["ca-central-1"],
    )


def test_create_get_roundtrip():
    repo = InMemoryAccountRequestRepository()
    req = _request()
    repo.create(req)
    loaded, version = repo.get(req.request_id)
    assert version == 1
    assert loaded.account_name == "payments-prod"


def test_duplicate_idempotency_key_rejected():
    repo = InMemoryAccountRequestRepository()
    repo.create(_request(idempotency_key="idem-key-dup"))
    with pytest.raises(DuplicateRequestError) as exc:
        repo.create(_request(idempotency_key="idem-key-dup"))
    assert exc.value.idempotency_key == "idem-key-dup"


def test_get_by_idempotency_key():
    repo = InMemoryAccountRequestRepository()
    req = _request(idempotency_key="idem-key-lookup")
    repo.create(req)
    found = repo.get_by_idempotency_key("idem-key-lookup")
    assert found is not None and found.request_id == req.request_id
    assert repo.get_by_idempotency_key("nope") is None


def test_optimistic_lock():
    repo = InMemoryAccountRequestRepository()
    req = _request()
    repo.create(req)
    loaded, version = repo.get(req.request_id)
    loaded.require_approval()
    repo.update(loaded, expected_version=version)  # 1 -> 2
    with pytest.raises(ConcurrencyError):
        repo.update(loaded, expected_version=1)  # stale


def test_stored_copy_is_isolated_from_caller_mutation():
    repo = InMemoryAccountRequestRepository()
    req = _request()
    repo.create(req)
    # Mutating the object we passed in must not change stored state.
    req.require_approval()
    stored, _ = repo.get(req.request_id)
    assert stored.state.value == "SUBMITTED"


def test_missing_raises():
    repo = InMemoryAccountRequestRepository()
    with pytest.raises(AccountNotFoundError):
        repo.get("req-missing")


def test_audit_append_and_order():
    repo = InMemoryAccountRequestRepository()
    req = _request()
    repo.create(req)
    repo.append_audit(
        AuditEvent(request_id=req.request_id, action=AuditAction.REQUEST_SUBMITTED, actor="a")
    )
    repo.append_audit(
        AuditEvent(request_id=req.request_id, action=AuditAction.APPROVAL_GRANTED, actor="b")
    )
    trail = repo.get_audit(req.request_id)
    assert [e.action for e in trail] == [
        AuditAction.REQUEST_SUBMITTED,
        AuditAction.APPROVAL_GRANTED,
    ]


def test_file_persistence_roundtrip(tmp_path):
    store = tmp_path / "state.json"
    repo = InMemoryAccountRequestRepository(persist_path=store)
    req = _request()
    repo.create(req)
    repo.append_audit(
        AuditEvent(request_id=req.request_id, action=AuditAction.REQUEST_SUBMITTED, actor="a")
    )

    # A fresh repo pointed at the same file must see the persisted state.
    reloaded = InMemoryAccountRequestRepository(persist_path=store)
    loaded, version = reloaded.get(req.request_id)
    assert version == 1
    assert loaded.request_id == req.request_id
    assert len(reloaded.get_audit(req.request_id)) == 1
