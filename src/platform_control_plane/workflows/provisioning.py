"""Workflow orchestrators for Step Functions integration.

These functions act as the glue between the API/events layer and the Step
Functions state machines, mapping domain requests into workflow executions.
"""

import json
from typing import Any

import boto3

from platform_control_plane.domain.account_request import AccountRequest
from platform_control_plane.telemetry.logging import get_logger

logger = get_logger(__name__)


class ProvisioningWorkflowOrchestrator:
    """Triggers and monitors the Step Functions provisioning workflow."""

    def __init__(self, state_machine_arn: str, sfn_client: Any = None) -> None:
        self.state_machine_arn = state_machine_arn
        self._client = sfn_client or boto3.client("stepfunctions")

    def start_provisioning(self, request: AccountRequest) -> str:
        """Start the provisioning workflow for an approved request.

        Returns:
            The execution ARN of the started workflow.
        """
        logger.info(
            "starting_provisioning_workflow",
            request_id=request.request_id,
            account_name=request.account_name,
        )

        payload = {
            "request_id": request.request_id,
            "account_name": request.account_name,
            "owner": request.owner,
            "environment": request.environment,
            "regions": request.regions,
            "monthly_budget": request.monthly_budget,
        }

        # Use the request_id as the execution name to ensure idempotency
        # at the Step Functions layer (only one execution per request_id).
        execution_name = f"provision-{request.request_id}"

        response = self._client.start_execution(
            stateMachineArn=self.state_machine_arn,
            name=execution_name,
            input=json.dumps(payload),
        )

        execution_arn = response["executionArn"]
        logger.info("provisioning_workflow_started", execution_arn=execution_arn)

        return str(execution_arn)
