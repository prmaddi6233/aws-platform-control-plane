"""Decommissioning workflow orchestrator for Step Functions integration."""

import json
from typing import Any

import boto3

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.telemetry.logging import get_logger

logger = get_logger(__name__)


class DecommissioningWorkflowOrchestrator:
    """Triggers and monitors the Step Functions decommissioning workflow."""

    def __init__(self, state_machine_arn: str, sfn_client: Any = None) -> None:
        self.state_machine_arn = state_machine_arn
        self._client = sfn_client or boto3.client("stepfunctions")

    def start_decommission(self, request: AccountRequest, requested_by: str) -> str:
        """Start the decommissioning workflow for an account.

        Returns:
            The execution ARN of the started workflow.
        """
        logger.info(
            "starting_decommissioning_workflow",
            request_id=request.request_id,
            account_id=request.aws_account_id,
            requested_by=requested_by,
        )

        if not request.aws_account_id:
            raise ValueError("Cannot decommission an account that has no AWS account ID")

        payload = {
            "request_id": request.request_id,
            "aws_account_id": request.aws_account_id,
            "account_name": request.account_name,
            "requested_by": requested_by,
        }

        # Execution name ensures only one decommission workflow can run per account
        execution_name = f"decom-{request.request_id}"

        response = self._client.start_execution(
            stateMachineArn=self.state_machine_arn,
            name=execution_name,
            input=json.dumps(payload),
        )

        execution_arn = response["executionArn"]
        logger.info("decommissioning_workflow_started", execution_arn=execution_arn)

        return str(execution_arn)
