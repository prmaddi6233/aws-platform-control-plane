"""DynamoDB adapter for the decommissioning aggregate.

Shares the physical single table with the account-request adapter using
entity-prefixed keys (see ADR 005). Enforces idempotency and one active
decommission per account via conditional transactional writes.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from platform_control_plane.domain.audit import GENESIS_HASH, AuditEvent, seal_event
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
from platform_control_plane.repositories.decommission_base import (
    AbstractDecommissioningRequestRepository,
)
from platform_control_plane.telemetry.logging import get_logger

logger = get_logger(__name__)

_ACTIVE_SK = "ACTIVE_DECOMMISSION"
_IDEMPOTENCY_SK = "DECOMMISSION_REQUEST"


class DynamoDecommissioningRepository(AbstractDecommissioningRequestRepository):
    """DynamoDB-backed decommissioning repository (shared single table)."""

    def __init__(self, table_name: str, dynamodb_client: Any = None) -> None:
        self.table_name = table_name
        self._client = dynamodb_client or boto3.client("dynamodb")

    def _metadata_item(self, request: DecommissioningRequest, version: int) -> dict[str, Any]:
        return {
            "PK": {"S": f"REQUEST#{request.decommission_id}"},
            "SK": {"S": "METADATA"},
            "Type": {"S": "DecommissioningRequest"},
            "Data": {"S": json.dumps(request.model_dump(mode="json"))},
            "Version": {"N": str(version)},
            "State": {"S": request.state.value},
            "AccountId": {"S": request.aws_account_id},
            "IdempotencyKey": {"S": request.idempotency_key},
        }

    def _from_item(self, item: dict[str, Any]) -> tuple[DecommissioningRequest, int]:
        data = json.loads(item["Data"]["S"])
        return DecommissioningRequest(**data), int(item["Version"]["N"])

    def create(self, request: DecommissioningRequest) -> None:
        logger.info("creating_decommission", decommission_id=request.decommission_id)
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._metadata_item(request, 1),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "PK": {"S": f"IDEMPOTENCY#{request.idempotency_key}"},
                                "SK": {"S": _IDEMPOTENCY_SK},
                                "RequestId": {"S": request.decommission_id},
                            },
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": {
                                "PK": {"S": f"ACCOUNT#{request.aws_account_id}"},
                                "SK": {"S": _ACTIVE_SK},
                                "RequestId": {"S": request.decommission_id},
                            },
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                reasons = e.response.get("CancellationReasons", [])
                if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                    existing = self.get_by_idempotency_key(request.idempotency_key)
                    raise DuplicateRequestError(
                        "Duplicate decommissioning request detected",
                        idempotency_key=request.idempotency_key,
                        existing_request_id=existing.decommission_id if existing else "unknown",
                    ) from e
                if len(reasons) > 2 and reasons[2].get("Code") == "ConditionalCheckFailed":
                    active = self.get_active_for_account(request.aws_account_id)
                    raise ActiveDecommissionExistsError(
                        "An active decommissioning request already exists for this account",
                        aws_account_id=request.aws_account_id,
                        existing_decommission_id=active.decommission_id if active else "unknown",
                    ) from e
            raise

    def get(self, decommission_id: str) -> tuple[DecommissioningRequest, int]:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": f"REQUEST#{decommission_id}"}, "SK": {"S": "METADATA"}},
        )
        item = response.get("Item")
        if not item:
            raise AccountNotFoundError(f"Decommission {decommission_id} not found")
        return self._from_item(item)

    def get_by_idempotency_key(self, idempotency_key: str) -> DecommissioningRequest | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": f"IDEMPOTENCY#{idempotency_key}"}, "SK": {"S": _IDEMPOTENCY_SK}},
        )
        item = response.get("Item")
        if not item:
            return None
        return self.get(item["RequestId"]["S"])[0]

    def get_active_for_account(self, aws_account_id: str) -> DecommissioningRequest | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": f"ACCOUNT#{aws_account_id}"}, "SK": {"S": _ACTIVE_SK}},
        )
        item = response.get("Item")
        if not item:
            return None
        return self.get(item["RequestId"]["S"])[0]

    def update(self, request: DecommissioningRequest, expected_version: int) -> None:
        new_version = expected_version + 1
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=self._metadata_item(request, new_version),
                ConditionExpression="attribute_exists(PK) AND Version = :v",
                ExpressionAttributeValues={":v": {"N": str(expected_version)}},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConcurrencyError(
                    f"Optimistic lock failed for decommission {request.decommission_id}"
                ) from e
            raise
        # Once terminal, release the account's active-decommission slot.
        if request.state in TERMINAL_STATES:
            self._client.delete_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"ACCOUNT#{request.aws_account_id}"}, "SK": {"S": _ACTIVE_SK}},
            )

    def list_all(self) -> list[DecommissioningRequest]:
        paginator = self._client.get_paginator("scan")
        requests: list[DecommissioningRequest] = []
        for page in paginator.paginate(
            TableName=self.table_name,
            FilterExpression="#t = :t",
            ExpressionAttributeNames={"#t": "Type"},
            ExpressionAttributeValues={":t": {"S": "DecommissioningRequest"}},
        ):
            for item in page.get("Items", []):
                requests.append(self._from_item(item)[0])
        return requests

    def append_audit(self, event: AuditEvent) -> None:
        chain = self.get_audit(event.request_id)
        prev_hash = chain[-1].event_hash if chain else GENESIS_HASH
        sealed = seal_event(event, sequence=len(chain) + 1, prev_hash=prev_hash)
        self._client.put_item(
            TableName=self.table_name,
            Item={
                "PK": {"S": f"REQUEST#{sealed.request_id}"},
                "SK": {"S": f"AUDIT#{sealed.sequence:012d}#{sealed.audit_id}"},
                "Type": {"S": "AuditEvent"},
                "Data": {"S": json.dumps(sealed.model_dump(mode="json"))},
            },
            ConditionExpression="attribute_not_exists(SK)",
        )

    def get_audit(self, decommission_id: str) -> list[AuditEvent]:
        response = self._client.query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"REQUEST#{decommission_id}"},
                ":prefix": {"S": "AUDIT#"},
            },
        )
        events = [AuditEvent(**json.loads(item["Data"]["S"])) for item in response.get("Items", [])]
        return sorted(events, key=lambda e: e.sequence)
