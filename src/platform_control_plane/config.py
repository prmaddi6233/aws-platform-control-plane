"""Validated runtime configuration for the control plane."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, Field


class ControlPlaneConfig(BaseModel):
    """Configuration for service behaviour. Validated at construction."""

    approval_ttl_minutes: int = Field(
        default=60,
        gt=0,
        description="Minutes a request may wait for approval before it can be expired.",
    )

    quarantine_retention_days: int = Field(
        default=7,
        gt=0,
        description="Days a decommissioned account is quarantined before closure.",
    )
    residual_lookback_days: int = Field(
        default=3,
        gt=0,
        description="Trailing days of spend inspected for residual-spend detection.",
    )
    residual_threshold_daily: float = Field(
        default=1.0,
        ge=0,
        description="Daily spend (USD) above which closure is blocked as residual.",
    )

    @property
    def approval_ttl(self) -> timedelta:
        return timedelta(minutes=self.approval_ttl_minutes)

    @property
    def quarantine_retention(self) -> timedelta:
        return timedelta(days=self.quarantine_retention_days)
