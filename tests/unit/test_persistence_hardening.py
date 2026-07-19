"""Persistence + audit-integrity hardening tests (items 8 & 9)."""

import json

import pytest

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.domain.audit import (
    AuditAction,
    AuditEvent,
    seal_event,
    verify_chain,
)
from platform_control_plane.exceptions import (
    AuditIntegrityError,
    StoreCorruptError,
    UnsupportedStoreVersionError,
)
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository


def _request(key="idem-persist-0001") -> AccountRequest:
    return AccountRequest(
        idempotency_key=key,
        account_name="payments-prod",
        owner="team@example.com",
        environment="production",
        data_classification="confidential",
        monthly_budget=5000,
        regions=["ca-central-1"],
    )


# ── file store: atomicity, versioning, corruption ───────────────────────────
def test_repository_restart_reloads_state(tmp_path):
    store = tmp_path / "state.json"
    repo = InMemoryAccountRequestRepository(persist_path=store)
    req = _request()
    repo.create(req)
    repo.append_audit(
        AuditEvent(request_id=req.request_id, action=AuditAction.REQUEST_SUBMITTED, actor="a")
    )

    reopened = InMemoryAccountRequestRepository(persist_path=store)
    loaded, version = reopened.get(req.request_id)
    assert version == 1 and loaded.request_id == req.request_id
    assert len(reopened.get_audit(req.request_id)) == 1


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = tmp_path / "state.json"
    repo = InMemoryAccountRequestRepository(persist_path=store)
    repo.create(_request())
    # Only the final store file should exist — no .tmp partials.
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_corrupt_store_raises_not_silently_discarded(tmp_path):
    store = tmp_path / "state.json"
    store.write_text("{ this is not valid json")
    with pytest.raises(StoreCorruptError):
        InMemoryAccountRequestRepository(persist_path=store)


def test_missing_version_header_is_corrupt(tmp_path):
    store = tmp_path / "state.json"
    store.write_text(json.dumps({"requests": {}}))
    with pytest.raises(StoreCorruptError):
        InMemoryAccountRequestRepository(persist_path=store)


def test_unsupported_store_version_raises(tmp_path):
    store = tmp_path / "state.json"
    store.write_text(
        json.dumps({"_store_version": 999, "requests": {}, "idempotency": {}, "audit": {}})
    )
    with pytest.raises(UnsupportedStoreVersionError):
        InMemoryAccountRequestRepository(persist_path=store)


# ── audit integrity ─────────────────────────────────────────────────────────
def test_audit_events_are_frozen():
    event = AuditEvent(request_id="req-1", action=AuditAction.REQUEST_SUBMITTED, actor="a")
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen raises ValidationError
        event.actor = "b"  # type: ignore[misc]


def test_duplicate_audit_id_rejected():
    repo = InMemoryAccountRequestRepository()
    repo.create(_request())
    e1 = AuditEvent(
        audit_id="aud-dup", request_id="req-x", action=AuditAction.REQUEST_SUBMITTED, actor="a"
    )
    e2 = AuditEvent(
        audit_id="aud-dup", request_id="req-x", action=AuditAction.APPROVAL_GRANTED, actor="b"
    )
    repo.append_audit(e1)
    with pytest.raises(AuditIntegrityError):
        repo.append_audit(e2)


def test_audit_chain_is_sequenced_and_verifiable():
    repo = InMemoryAccountRequestRepository()
    repo.create(_request())
    for action in (
        AuditAction.REQUEST_SUBMITTED,
        AuditAction.POLICY_EVALUATED,
        AuditAction.APPROVAL_GRANTED,
    ):
        repo.append_audit(AuditEvent(request_id="req-x", action=action, actor="a"))
    chain = repo.get_audit("req-x")
    assert [e.sequence for e in chain] == [1, 2, 3]
    assert verify_chain(chain) is True


def test_tampering_breaks_the_chain():
    repo = InMemoryAccountRequestRepository()
    repo.create(_request())
    repo.append_audit(
        AuditEvent(request_id="req-x", action=AuditAction.REQUEST_SUBMITTED, actor="a")
    )
    repo.append_audit(
        AuditEvent(request_id="req-x", action=AuditAction.APPROVAL_GRANTED, actor="b")
    )
    chain = repo.get_audit("req-x")
    # Forge the second event's actor without recomputing the hash.
    forged = chain[1].model_copy(update={"actor": "attacker"})
    tampered = [chain[0], forged]
    assert verify_chain(tampered) is False


def test_reordering_breaks_the_chain():
    repo = InMemoryAccountRequestRepository()
    repo.create(_request())
    repo.append_audit(
        AuditEvent(request_id="req-x", action=AuditAction.REQUEST_SUBMITTED, actor="a")
    )
    repo.append_audit(
        AuditEvent(request_id="req-x", action=AuditAction.APPROVAL_GRANTED, actor="b")
    )
    chain = repo.get_audit("req-x")
    assert verify_chain([chain[1], chain[0]]) is False


def test_seal_event_is_deterministic():
    e = AuditEvent(
        audit_id="aud-1", request_id="req-1", action=AuditAction.REQUEST_SUBMITTED, actor="a"
    )
    s1 = seal_event(e, sequence=1, prev_hash="0" * 64)
    s2 = seal_event(e, sequence=1, prev_hash="0" * 64)
    assert s1.event_hash == s2.event_hash
