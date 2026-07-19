"""Moto-backed tests for the DynamoDB decommissioning adapter (shared table)."""

import boto3
import pytest
from moto import mock_aws

from platform_control_plane.domain.audit import AuditAction, AuditEvent, verify_chain
from platform_control_plane.domain.decommission import DecommissioningRequest
from platform_control_plane.exceptions import (
    ActiveDecommissionExistsError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories.decommission_dynamodb import (
    DynamoDecommissioningRepository,
)

TABLE = "platform-control-plane-requests-test"


@pytest.fixture
def repo():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ca-central-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoDecommissioningRepository(TABLE, dynamodb_client=client)


def _req(key="idem-dyn-1", account="111111111111"):
    return DecommissioningRequest(
        idempotency_key=key, aws_account_id=account, requested_by="o@example.com"
    )


def test_create_get_roundtrip(repo):
    r = _req()
    repo.create(r)
    loaded, version = repo.get(r.decommission_id)
    assert version == 1 and loaded.aws_account_id == "111111111111"


def test_duplicate_idempotency_key_rejected(repo):
    repo.create(_req(key="dup", account="111111111111"))
    with pytest.raises(DuplicateRequestError):
        repo.create(_req(key="dup", account="222222222222"))


def test_second_active_decommission_for_account_rejected(repo):
    repo.create(_req(key="a", account="111111111111"))
    with pytest.raises(ActiveDecommissionExistsError):
        repo.create(_req(key="b", account="111111111111"))


def test_optimistic_lock(repo):
    r = _req()
    repo.create(r)
    loaded, version = repo.get(r.decommission_id)
    loaded.begin_validation("approver@example.com")
    repo.update(loaded, expected_version=version)  # 1 -> 2
    with pytest.raises(ConcurrencyError):
        repo.update(loaded, expected_version=1)


def test_audit_chain_persisted_and_verifiable(repo):
    r = _req()
    repo.create(r)
    for action in (AuditAction.DECOMMISSION_REQUESTED, AuditAction.DECOMMISSION_STATE_CHANGED):
        repo.append_audit(AuditEvent(request_id=r.decommission_id, action=action, actor="a"))
    chain = repo.get_audit(r.decommission_id)
    assert [e.sequence for e in chain] == [1, 2]
    assert verify_chain(chain) is True
