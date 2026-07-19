"""Tests for the policy engine and the validation wrapper."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from platform_control_plane.api.schemas import CreateAccountRequest
from platform_control_plane.exceptions import PolicyViolationError
from platform_control_plane.policies.engine import PolicyEngine
from platform_control_plane.policies.rules import PolicyEffect
from platform_control_plane.policies.validation import validate_account_request

ENGINE = PolicyEngine()


def _request(**overrides) -> CreateAccountRequest:
    base = dict(
        idempotency_key="idem-key-baseline",
        account_name="payments-prod",
        owner="team@example.com",
        environment="production",
        data_classification="confidential",
        monthly_budget=500,
        regions=["us-east-1", "ca-central-1"],
    )
    base.update(overrides)
    return CreateAccountRequest(**base)


def test_valid_request_is_allowed():
    result = ENGINE.evaluate(_request())
    assert result.allowed is True
    assert result.denials == []
    assert result.summary == "ALLOW"
    # Wrapper must not raise.
    validate_account_request(_request())


def test_budget_floor_denies_with_remediation():
    result = ENGINE.evaluate(
        _request(
            idempotency_key="idem-key-budget-low",
            environment="sandbox",
            data_classification="public",
            monthly_budget=25,
        )
    )
    assert result.allowed is False
    denial = next(d for d in result.denials if d.policy_id == "BUDGET-001")
    assert denial.decision == PolicyEffect.DENY
    assert denial.remediation  # remediation guidance is always present


def test_nonprod_budget_ceiling():
    result = ENGINE.evaluate(
        _request(
            idempotency_key="idem-key-budget-high",
            environment="development",
            data_classification="internal",
            monthly_budget=15000,
        )
    )
    assert any(d.policy_id == "BUDGET-002" for d in result.denials)


def test_confidential_data_in_sandbox():
    result = ENGINE.evaluate(
        _request(
            idempotency_key="idem-key-data",
            environment="sandbox",
            data_classification="restricted",
            monthly_budget=500,
        )
    )
    assert any(d.policy_id == "DATA-001" for d in result.denials)


def test_unapproved_region():
    result = ENGINE.evaluate(
        _request(
            idempotency_key="idem-key-region",
            environment="production",
            data_classification="public",
            regions=["ap-southeast-2"],
        )
    )
    assert any(d.policy_id == "REGION-001" for d in result.denials)


def test_multiple_violations_are_all_reported():
    result = ENGINE.evaluate(
        _request(
            idempotency_key="idem-key-multi",
            environment="sandbox",
            data_classification="restricted",
            monthly_budget=15000,
            regions=["ap-southeast-2"],
        )
    )
    ids = {d.policy_id for d in result.denials}
    # budget ceiling, data classification, and region should all fire.
    assert {"BUDGET-002", "DATA-001", "REGION-001"} <= ids


def test_validation_wrapper_raises_on_denial():
    with pytest.raises(PolicyViolationError) as exc:
        validate_account_request(
            _request(
                idempotency_key="idem-key-deny",
                environment="production",
                data_classification="public",
                regions=["ap-southeast-2"],
            )
        )
    assert exc.value.violations[0]["policy_id"] == "REGION-001"


def test_schema_rejects_reserved_account_name():
    with pytest.raises(PydanticValidationError) as exc:
        _request(idempotency_key="idem-key-badname", account_name="aws-core")
    assert "Account names cannot start with 'aws-'" in str(exc.value)
