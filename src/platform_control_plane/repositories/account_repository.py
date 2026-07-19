"""DynamoDB repository for persisting AccountRequest entities.

Implements the repository pattern with optimistic locking for concurrency
control and idempotency checking on insert.
"""

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.domain.audit import GENESIS_HASH, AuditEvent, seal_event
from platform_control_plane.exceptions import (
    AccountNotFoundError,
    ConcurrencyError,
    DuplicateRequestError,
)
from platform_control_plane.repositories.base import AbstractAccountRequestRepository
from platform_control_plane.telemetry.logging import get_logger

logger = get_logger(__name__)


class AccountRequestRepository(AbstractAccountRequestRepository):
    """DynamoDB-backed repository for Account Requests.

    Single-table design (see docs/dynamodb-data-model.md):
    - ``PK=REQ#<id>, SK=METADATA``        — request state (version-checked)
    - ``PK=IDEMPOTENCY#<key>, SK=...``     — idempotency guard
    - ``PK=REQ#<id>, SK=AUDIT#<ts>#<id>``  — append-only audit trail
    """

    def __init__(self, table_name: str, dynamodb_client: Any = None) -> None:
        self.table_name = table_name
        self._client = dynamodb_client or boto3.client("dynamodb")

    def _to_item(self, request: AccountRequest, version: int) -> dict[str, Any]:
        """Convert domain entity to DynamoDB item."""
        # Convert Pydantic model to dict, making datetimes strings
        data = request.model_dump(mode="json")

        # We store the main record keyed by request_id
        item = {
            "PK": {"S": f"REQ#{request.request_id}"},
            "SK": {"S": "METADATA"},
            "Data": {"S": json.dumps(data)},
            "Version": {"N": str(version)},
            "State": {"S": request.state.value},
            "IdempotencyKey": {"S": request.idempotency_key},
            "Type": {"S": "AccountRequest"},
        }
        return item

    def _from_item(self, item: dict[str, Any]) -> tuple[AccountRequest, int]:
        """Convert DynamoDB item back to domain entity."""
        data = json.loads(item["Data"]["S"])
        version = int(item["Version"]["N"])
        return AccountRequest(**data), version

    def create(self, request: AccountRequest) -> None:
        """Create a new account request, enforcing idempotency."""
        logger.info("creating_account_request", request_id=request.request_id)

        item = self._to_item(request, version=1)

        # We use a transact_write to write the main record AND an idempotency record.
        # If the idempotency record already exists, the transaction fails.
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "PK": {"S": f"IDEMPOTENCY#{request.idempotency_key}"},
                                "SK": {"S": "ACCOUNT_REQUEST"},
                                "RequestId": {"S": request.request_id},
                            },
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "TransactionCanceledException":
                reasons = e.response.get("CancellationReasons", [])
                # Check if the idempotency record failed (index 1)
                if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                    # Retrieve the existing request ID to return to the caller
                    existing = self._client.get_item(
                        TableName=self.table_name,
                        Key={
                            "PK": {"S": f"IDEMPOTENCY#{request.idempotency_key}"},
                            "SK": {"S": "ACCOUNT_REQUEST"},
                        },
                    )
                    existing_id = existing.get("Item", {}).get("RequestId", {}).get("S", "unknown")
                    raise DuplicateRequestError(
                        "Duplicate account request detected",
                        idempotency_key=request.idempotency_key,
                        existing_request_id=existing_id,
                    ) from e
            raise

    def get(self, request_id: str) -> tuple[AccountRequest, int]:
        """Retrieve an account request and its current version."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"REQ#{request_id}"},
                "SK": {"S": "METADATA"},
            },
        )

        item = response.get("Item")
        if not item:
            raise AccountNotFoundError(f"Account request {request_id} not found")

        return self._from_item(item)

    def update(self, request: AccountRequest, expected_version: int) -> None:
        """Update an account request using optimistic locking."""
        new_version = expected_version + 1
        item = self._to_item(request, new_version)

        logger.debug(
            "updating_account_request",
            request_id=request.request_id,
            state=request.state,
            expected_version=expected_version,
            new_version=new_version,
        )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_exists(PK) AND Version = :v",
                ExpressionAttributeValues={":v": {"N": str(expected_version)}},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConcurrencyError(
                    f"Optimistic lock failed for request {request.request_id}"
                ) from e
            raise

    def get_by_idempotency_key(self, idempotency_key: str) -> AccountRequest | None:
        """Resolve an idempotency key to its owning request, if any."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IDEMPOTENCY#{idempotency_key}"},
                "SK": {"S": "ACCOUNT_REQUEST"},
            },
        )
        item = response.get("Item")
        if not item:
            return None
        request_id = item["RequestId"]["S"]
        request, _ = self.get(request_id)
        return request

    def list_all(self) -> list[AccountRequest]:
        """Scan for all request metadata records.

        A scan is acceptable for an operator listing; production dashboards
        should use a GSI on ``Type``/``State`` rather than scanning at scale.
        """
        paginator = self._client.get_paginator("scan")
        requests: list[AccountRequest] = []
        for page in paginator.paginate(
            TableName=self.table_name,
            FilterExpression="#t = :t",
            ExpressionAttributeNames={"#t": "Type"},
            ExpressionAttributeValues={":t": {"S": "AccountRequest"}},
        ):
            for item in page.get("Items", []):
                requests.append(self._from_item(item)[0])
        return requests

    def append_audit(self, event: AuditEvent) -> None:
        """Append a sealed, hash-chained audit item under the request partition.

        The sequence number and previous hash come from the existing chain; the
        conditional write on a zero-padded sequence SK prevents two writers from
        claiming the same position.
        """
        chain = self.get_audit(event.request_id)
        prev_hash = chain[-1].event_hash if chain else GENESIS_HASH
        sealed = seal_event(event, sequence=len(chain) + 1, prev_hash=prev_hash)
        self._client.put_item(
            TableName=self.table_name,
            Item={
                "PK": {"S": f"REQ#{sealed.request_id}"},
                "SK": {"S": f"AUDIT#{sealed.sequence:012d}#{sealed.audit_id}"},
                "Type": {"S": "AuditEvent"},
                "Data": {"S": json.dumps(sealed.model_dump(mode="json"))},
            },
            # No two events may occupy the same sequence position.
            ConditionExpression="attribute_not_exists(SK)",
        )

    def get_audit(self, request_id: str) -> list[AuditEvent]:
        """Query the audit trail for a request, ordered by sequence."""
        response = self._client.query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"REQ#{request_id}"},
                ":prefix": {"S": "AUDIT#"},
            },
        )
        events = [AuditEvent(**json.loads(item["Data"]["S"])) for item in response.get("Items", [])]
        return sorted(events, key=lambda e: e.sequence)
