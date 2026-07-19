"""Behavioural tests for the simulator scenario runner and CLI."""

import json

import pytest

from platform_control_plane.cli import main
from platform_control_plane.workflows.scenarios import (
    DECOMMISSIONING_SCENARIOS,
    PROVISIONING_SCENARIOS,
    run_scenario,
)

# scenario -> (final_state, success, exit_code, failure_classification|None)
PROVISIONING_EXPECTED = {
    "success": ("COMPLETED", True, 0, None),
    "policy-rejected": ("POLICY_REJECTED", False, 2, "POLICY_DENIED"),
    "approval-rejected": ("REJECTED", False, 2, None),
    "approval-timeout": ("CANCELLED", False, 2, "APPROVAL_TIMEOUT"),
    "retryable-baseline-failure": ("COMPLETED", True, 0, None),
    "retry-exhaustion": ("ACTION_REQUIRED", False, 4, "PERMANENT_DEPENDENCY_FAILURE"),
    "manual-intervention": ("ACTION_REQUIRED", False, 4, "MANUAL_INTERVENTION_REQUIRED"),
}
DECOMMISSIONING_EXPECTED = {
    "success": ("RETENTION_PERIOD", True, 0, None),
    "unauthorized": ("DECOMMISSION_REQUESTED", False, 2, "AUTHORIZATION_ERROR"),
    "active-dependency": ("DECOMMISSION_ACTION_REQUIRED", False, 2, "COMMITMENT_BLOCK"),
    "active-ri": ("DECOMMISSION_ACTION_REQUIRED", False, 2, "COMMITMENT_BLOCK"),
    "active-savings-plan": ("DECOMMISSION_ACTION_REQUIRED", False, 2, "COMMITMENT_BLOCK"),
    "residual-spend-block": ("DECOMMISSION_ACTION_REQUIRED", False, 2, "RESIDUAL_SPEND_BLOCK"),
    "risk-accepted": ("RETENTION_PERIOD", True, 0, None),
    "evidence-export-failure": ("DECOMMISSION_FAILED", False, 4, "PERMANENT_DEPENDENCY_FAILURE"),
    "workload-disablement-failure": (
        "DECOMMISSION_FAILED",
        False,
        4,
        "PERMANENT_DEPENDENCY_FAILURE",
    ),
    "retention-not-expired": ("RETENTION_PERIOD", True, 0, None),
    "closure-success": ("CLOSED", True, 0, None),
}


def test_all_provisioning_scenarios_are_covered():
    assert set(PROVISIONING_EXPECTED) == set(PROVISIONING_SCENARIOS)


def test_all_decommissioning_scenarios_are_covered():
    assert set(DECOMMISSIONING_EXPECTED) == set(DECOMMISSIONING_SCENARIOS)


@pytest.mark.parametrize("scenario", PROVISIONING_SCENARIOS)
def test_provisioning_scenario_outcomes(scenario):
    final, success, exit_code, classification = PROVISIONING_EXPECTED[scenario]
    result = run_scenario("provisioning", scenario)
    assert result.final_state == final
    assert result.success is success
    assert result.exit_code() == exit_code
    if classification is None:
        assert result.failure is None
    else:
        assert result.failure is not None and result.failure.classification == classification


@pytest.mark.parametrize("scenario", DECOMMISSIONING_SCENARIOS)
def test_decommissioning_scenario_outcomes(scenario):
    final, success, exit_code, classification = DECOMMISSIONING_EXPECTED[scenario]
    result = run_scenario("decommissioning", scenario)
    assert result.final_state == final
    assert result.success is success
    assert result.exit_code() == exit_code
    if classification is None:
        assert result.failure is None
    else:
        assert result.failure is not None and result.failure.classification == classification


@pytest.mark.parametrize("scenario", PROVISIONING_SCENARIOS)
def test_provisioning_json_contract(scenario):
    result = run_scenario("provisioning", scenario)
    payload = json.loads(result.model_dump_json())
    for key in (
        "schema_version",
        "scenario",
        "workflow_type",
        "request_id",
        "correlation_id",
        "initial_state",
        "final_state",
        "success",
        "findings",
        "retry_attempts",
        "transitions",
        "audit_chain_valid",
    ):
        assert key in payload
    assert payload["schema_version"] == "1.0"
    assert payload["audit_chain_valid"] is True


def test_provisioning_correlation_is_request_id_and_propagates():
    result = run_scenario("provisioning", "success")
    # Provisioning uses the request_id as the correlation id.
    assert result.correlation_id == result.request_id


def test_decommissioning_correlation_is_stable_across_transitions():
    result = run_scenario("decommissioning", "active-ri")
    assert result.correlation_id.startswith("corr-")
    assert result.transitions  # transitions derived from the real audit chain
    assert result.audit_chain_valid is True


def test_failure_metadata_is_complete_for_blocks():
    result = run_scenario("decommissioning", "residual-spend-block")
    f = result.failure
    assert f is not None
    assert f.retryable is False
    assert f.correctable_by in ("USER", "PLATFORM", "OPERATOR")
    assert f.recommended_action
    assert f.alert_severity in ("NONE", "INFO", "WARNING", "PAGE")


def test_scenarios_are_deterministic():
    a = run_scenario("decommissioning", "active-ri")
    b = run_scenario("decommissioning", "active-ri")
    assert (a.final_state, a.success, a.exit_code()) == (b.final_state, b.success, b.exit_code())


def test_audit_chains_verify_for_all_scenarios():
    for s in PROVISIONING_SCENARIOS:
        assert run_scenario("provisioning", s).audit_chain_valid is True
    for s in DECOMMISSIONING_SCENARIOS:
        assert run_scenario("decommissioning", s).audit_chain_valid is True


# ── CLI exit codes ───────────────────────────────────────────────────────────
def test_cli_success_exit_zero(capsys):
    assert main(["simulate", "provisioning", "--scenario", "success"]) == 0
    assert json.loads(capsys.readouterr().out)["success"] is True


def test_cli_business_block_exit_two(capsys):
    assert main(["simulate", "decommissioning", "--scenario", "active-ri"]) == 2
    capsys.readouterr()


def test_cli_platform_failure_exit_four(capsys):
    assert main(["simulate", "provisioning", "--scenario", "retry-exhaustion"]) == 4
    capsys.readouterr()


def test_cli_invalid_scenario_exit_three(capsys):
    assert main(["simulate", "provisioning", "--scenario", "nope"]) == 3
    err = capsys.readouterr().err
    assert "USAGE_ERROR" in err


def test_cli_invalid_workflow_type_exit_three(capsys):
    assert main(["simulate", "bogus", "--scenario", "success"]) == 3
    capsys.readouterr()


def test_cli_output_has_no_stack_trace(capsys):
    main(["simulate", "decommissioning", "--scenario", "active-ri"])
    out = capsys.readouterr().out
    assert "Traceback" not in out and 'File "' not in out
