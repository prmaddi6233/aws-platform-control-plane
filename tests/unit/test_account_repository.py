"""Repository tests exercising idempotency and optimistic concurrency.

These run against a moto-mocked DynamoDB table, so they validate the actual
conditional-write and transactional idempotency behaviour — the central
correctness claim of the control plane — without a real AWS account.
"""

import boto3
import pytest
from moto import mock_aws

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.exceptions import (
    AccountNotFoundError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories.account_repository import (
    AccountRequestRepository,
)

TABLE_NAME = "platform-control-plane-requests-test"


def _make_request(idempotency_key: str = "idem-key-repo-0001") -> AccountRequest:
    return AccountRequest(
        idempotency_key=idempotency_key,
        account_name="payments-prod",
        owner="team@example.com",
        environment="production",
        data_classification="confidential",
        monthly_budget=5000,
        regions=["ca-central-1"],
    )


@pytest.fixture
def repo():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ca-central-1")
        client.create_table(
            TableName=TABLE_NAME,
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
        yield AccountRequestRepository(TABLE_NAME, dynamodb_client=client)


def test_create_and_get_roundtrip(repo):
    request = _make_request()
    repo.create(request)

    loaded, version = repo.get(request.request_id)
    assert version == 1
    assert loaded.request_id == request.request_id
    assert loaded.account_name == "payments-prod"
    assert loaded.idempotency_key == "idem-key-repo-0001"


def test_duplicate_idempotency_key_is_rejected(repo):
    first = _make_request(idempotency_key="idem-key-dup-shared")
    repo.create(first)

    # A different request_id but the SAME idempotency key must be rejected,
    # and the caller must be told which request already owns the key.
    second = _make_request(idempotency_key="idem-key-dup-shared")
    assert second.request_id != first.request_id

    with pytest.raises(DuplicateRequestError) as exc:
        repo.create(second)

    assert exc.value.idempotency_key == "idem-key-dup-shared"
    assert exc.value.existing_request_id == first.request_id


def test_optimistic_lock_blocks_stale_write(repo):
    request = _make_request()
    repo.create(request)

    loaded, version = repo.get(request.request_id)
    assert version == 1

    # First writer advances the version 1 -> 2.
    loaded.require_approval()
    repo.update(loaded, expected_version=version)

    # A second writer still holding version 1 must be rejected.
    loaded.approve("approver@example.com")
    with pytest.raises(ConcurrencyError):
        repo.update(loaded, expected_version=1)


def test_update_persists_state_transition(repo):
    request = _make_request()
    repo.create(request)
    loaded, version = repo.get(request.request_id)

    loaded.require_approval()
    repo.update(loaded, expected_version=version)

    reloaded, new_version = repo.get(request.request_id)
    assert new_version == 2
    assert reloaded.state.value == "PENDING_APPROVAL"


def test_get_missing_request_raises(repo):
    with pytest.raises(AccountNotFoundError):
        repo.get("req-doesnotexist")
