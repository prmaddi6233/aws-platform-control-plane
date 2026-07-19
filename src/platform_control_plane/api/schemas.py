"""API schemas for the platform control plane.

These models define the external contract for the API and validate
incoming requests before they reach the domain layer.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CreateAccountRequest(BaseModel):
    """Schema for submitting a new account request.

    Example:
        account_name: "payments-prod"
        owner: "payments-team@example.com"
        environment: "production"
        data_classification: "confidential"
        monthly_budget: 5000
        regions: ["ca-central-1"]
    """

    account_name: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Globally unique account name (e.g., payments-prod)",
    )
    owner: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$",
        description="Team email address or distribution list",
    )
    environment: Literal["sandbox", "development", "staging", "production"]
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    monthly_budget: int = Field(
        ...,
        gt=0,
        le=100000,
        description="Monthly budget in USD for alerting purposes",
    )
    regions: list[str] = Field(
        default_factory=lambda: ["us-east-1"],
        min_length=1,
        description="Allowed AWS regions for this account",
    )
    idempotency_key: str = Field(
        ...,
        min_length=10,
        max_length=64,
        description="Client-provided key to prevent duplicate provisioning",
    )

    @field_validator("account_name")
    @classmethod
    def validate_account_name(cls, v: str) -> str:
        """Ensure account names conform to AWS and internal standards."""
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(
                "Account name must contain only lowercase letters, numbers, and hyphens"
            )
        if v.startswith("aws-"):
            raise ValueError("Account names cannot start with 'aws-'")
        return v


class AccountResponse(BaseModel):
    """Response schema for account lookups and creations."""

    request_id: str
    account_name: str
    owner: str
    environment: str
    state: str
    aws_account_id: str | None = None
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    """Standardized error response format."""

    error_code: str
    message: str
    details: dict[str, str | list[str]] | None = None
