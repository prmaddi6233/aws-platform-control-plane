"""Tests that the failure taxonomy is complete and internally consistent."""

from platform_control_plane.domain.failures import (
    AlertSeverity,
    Correctability,
    FailureClass,
    all_profiles,
    profile_for,
)

VALID_EXIT_CODES = {0, 2, 3, 4}


def test_every_failure_class_has_a_profile():
    for fc in FailureClass:
        profile = profile_for(fc)
        assert profile.failure_class == fc


def test_profiles_have_complete_metadata():
    for p in all_profiles():
        assert isinstance(p.retryable, bool)
        assert isinstance(p.correctable_by, Correctability)
        assert isinstance(p.alert_severity, AlertSeverity)
        assert p.recommended_action.strip(), f"{p.failure_class} missing recommended_action"
        assert p.cli_exit_code in VALID_EXIT_CODES, f"{p.failure_class} bad exit code"


def test_registry_covers_exactly_the_enum():
    assert {p.failure_class for p in all_profiles()} == set(FailureClass)


def test_blocking_business_classes_map_to_exit_two():
    for fc in (
        FailureClass.POLICY_DENIED,
        FailureClass.COMMITMENT_BLOCK,
        FailureClass.RESIDUAL_SPEND_BLOCK,
        FailureClass.AUTHORIZATION_ERROR,
        FailureClass.APPROVAL_TIMEOUT,
    ):
        assert profile_for(fc).cli_exit_code == 2


def test_platform_failures_map_to_exit_four():
    for fc in (
        FailureClass.PERMANENT_DEPENDENCY_FAILURE,
        FailureClass.WORKFLOW_TIMEOUT,
        FailureClass.MANUAL_INTERVENTION_REQUIRED,
        FailureClass.CONCURRENCY_CONFLICT,
    ):
        assert profile_for(fc).cli_exit_code == 4


def test_exceptions_carry_valid_failure_classes():
    from platform_control_plane.exceptions import (
        ApprovalError,
        AuthorizationError,
        ConcurrencyError,
        DuplicateRequestError,
        IdempotencyConflictError,
        PolicyViolationError,
        RetryableStepError,
        WorkflowStepError,
    )

    for exc_cls in (
        ApprovalError,
        AuthorizationError,
        ConcurrencyError,
        DuplicateRequestError,
        IdempotencyConflictError,
        PolicyViolationError,
        RetryableStepError,
        WorkflowStepError,
    ):
        assert exc_cls.failure_class in set(FailureClass)
