"""Unit tests for the AccountRequest domain model."""

import pytest

from platform_control_plane.domain.account_request import AccountRequest, RequestState
from platform_control_plane.exceptions import StateTransitionError


def test_initial_state_emits_submitted_event():
    request = AccountRequest(
        idempotency_key="test-key-123",
        account_name="test-account",
        owner="team@example.com",
        environment="development",
        data_classification="internal",
        monthly_budget=500,
        regions=["us-east-1"],
    )

    assert request.state == RequestState.SUBMITTED

    events = request.pop_events()
    assert len(events) == 1

    event = events[0]
    assert event.event_type == "AccountRequestSubmitted"
    assert event.request_id == request.request_id


def test_require_approval_transition():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.pop_events()  # clear initial

    request.require_approval()
    assert request.state == RequestState.PENDING_APPROVAL
    assert len(request.pop_events()) == 0


def test_approve_transition():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.pop_events()

    request.approve("approver@example.com")
    assert request.state == RequestState.APPROVED

    events = request.pop_events()
    assert len(events) == 1
    assert events[0].event_type == "AccountRequestApproved"
    assert events[0].approved_by == "approver@example.com"


def test_start_provisioning_from_approved():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.approve("approver")
    request.pop_events()

    request.start_provisioning("exec-123")
    assert request.state == RequestState.PROVISIONING

    events = request.pop_events()
    assert len(events) == 1
    assert events[0].event_type == "AccountProvisioningStarted"


def test_mark_completed_from_provisioning():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.approve("approver")
    request.start_provisioning("exec-123")

    request.pop_events()
    request.mark_completed("111122223333")
    assert request.state == RequestState.COMPLETED
    assert request.aws_account_id == "111122223333"

    # Completion must emit a measurable event so the latency SLI
    # (approved -> completed) can be computed from telemetry.
    events = request.pop_events()
    assert len(events) == 1
    assert events[0].event_type == "AccountProvisioningCompleted"
    assert events[0].account_id == "111122223333"
    assert events[0].elapsed_seconds >= 0.0


def test_mark_action_required_from_provisioning():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.approve("approver")
    request.start_provisioning("exec-123")
    request.pop_events()

    # Account was created but baseline could not be fully applied.
    request.mark_action_required("ApplyBaseline", "SCP denied Config", "111122223333")
    assert request.state == RequestState.ACTION_REQUIRED
    assert request.aws_account_id == "111122223333"
    assert request.provisioning_error == "ApplyBaseline: SCP denied Config"

    events = request.pop_events()
    assert len(events) == 1
    assert events[0].event_type == "AccountProvisioningFailed"


def test_cannot_action_required_before_provisioning():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    with pytest.raises(StateTransitionError):
        request.mark_action_required("ApplyBaseline", "too early", "111122223333")


def test_mark_failed_from_provisioning():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )
    request.approve("approver")
    request.start_provisioning("exec-123")
    request.pop_events()

    request.mark_failed("CreateAccount", "ServiceUnavailable")
    assert request.state == RequestState.FAILED
    assert request.provisioning_error == "CreateAccount: ServiceUnavailable"

    events = request.pop_events()
    assert len(events) == 1
    assert events[0].event_type == "AccountProvisioningFailed"


def test_illegal_state_transition_raises_error():
    request = AccountRequest(
        idempotency_key="test-key",
        account_name="test",
        owner="test@test.com",
        environment="dev",
        data_classification="public",
        monthly_budget=100,
        regions=["us-east-1"],
    )

    # Cannot complete from SUBMITTED
    with pytest.raises(StateTransitionError) as exc_info:
        request.mark_completed("111122223333")

    assert exc_info.value.current_state == RequestState.SUBMITTED
    assert exc_info.value.attempted_state == RequestState.COMPLETED
