"""Contract tests for the Step Functions ASL definitions.

These guard the workflow's operational invariants without deploying to AWS:
every Task has a timeout, retries are bounded, waits are finite, and every
failure path reaches a terminal or manual-intervention state.
"""

import json
from pathlib import Path

import pytest

ASL_DIR = Path(__file__).resolve().parents[2] / "statemachines"
ASL_FILES = sorted(ASL_DIR.glob("*.asl.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _tasks(doc: dict) -> dict[str, dict]:
    return {n: s for n, s in doc["States"].items() if s.get("Type") == "Task"}


@pytest.fixture(params=ASL_FILES, ids=lambda p: p.name)
def machine(request):
    return _load(request.param)


def test_asl_files_exist():
    assert ASL_FILES, "no ASL definitions found"


def test_machine_has_execution_timeout(machine):
    assert isinstance(machine.get("TimeoutSeconds"), int)
    assert machine["TimeoutSeconds"] > 0


def test_every_task_has_a_timeout(machine):
    for name, state in _tasks(machine).items():
        assert isinstance(state.get("TimeoutSeconds"), int), f"{name} missing TimeoutSeconds"
        assert state["TimeoutSeconds"] > 0


def test_retries_are_bounded(machine):
    for name, state in _tasks(machine).items():
        for rule in state.get("Retry", []):
            attempts = rule.get("MaxAttempts")
            assert isinstance(attempts, int), f"{name} retry missing MaxAttempts"
            # 0 is allowed (no retry); infinite/unbounded is not.
            assert 0 <= attempts <= 10, f"{name} retry MaxAttempts unbounded"


def test_wait_states_are_finite(machine):
    for name, state in machine["States"].items():
        if state.get("Type") == "Wait":
            assert "Seconds" in state or "Timestamp" in state, f"{name} wait is unbounded"
            if "Seconds" in state:
                assert isinstance(state["Seconds"], int) and state["Seconds"] > 0


def _is_failure_handler(states: dict, state: dict) -> bool:
    """A task whose Next is a Fail state is itself the failure handler."""
    nxt = state.get("Next")
    return bool(nxt) and states.get(nxt, {}).get("Type") == "Fail"


def test_every_task_has_a_catch_to_a_known_state(machine):
    states = machine["States"]
    for name, state in _tasks(machine).items():
        if _is_failure_handler(states, state):
            continue  # the failure handler is the catch target, not a catcher
        catches = state.get("Catch", [])
        assert catches, f"{name} has no Catch handler"
        for c in catches:
            assert c["Next"] in states, f"{name} catches to unknown state {c['Next']}"


def _reaches_terminal(states: dict, start: str) -> bool:
    """Follow Next/terminal edges from ``start``; return True if a terminal is reachable."""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        state = states[current]
        stype = state.get("Type")
        if stype in ("Fail", "Succeed") or state.get("End") is True:
            return True
        if "Next" in state:
            frontier.append(state["Next"])
        for c in state.get("Catch", []):
            frontier.append(c["Next"])
    return False


def test_every_failure_path_reaches_a_terminal_state(machine):
    states = machine["States"]
    for name, state in _tasks(machine).items():
        for c in state.get("Catch", []):
            assert _reaches_terminal(states, c["Next"]), (
                f"{name} failure path via {c['Next']} does not reach a terminal state"
            )


def test_a_terminal_fail_state_exists(machine):
    types = {s.get("Type") for s in machine["States"].values()}
    assert "Fail" in types, "workflow has no explicit Fail state"


def test_correlation_identifier_propagates(machine):
    # request_id is the correlation carrier; it must appear in the definition.
    assert "request_id" in json.dumps(machine)


def test_backoff_is_explicit(machine):
    for name, state in _tasks(machine).items():
        for rule in state.get("Retry", []):
            assert "IntervalSeconds" in rule, f"{name} retry missing IntervalSeconds"
            assert "BackoffRate" in rule, f"{name} retry missing BackoffRate"
            assert rule["BackoffRate"] >= 1.0


def test_no_raw_placeholder_arns_for_task_functions(machine):
    # Task Lambdas must be injected as ${...} template variables, never a
    # hard-coded ARN, so the versioned ASL contains no real function ARN.
    for name, state in _tasks(machine).items():
        fn = state.get("Parameters", {}).get("FunctionName", "")
        assert fn.startswith("${") and fn.endswith("}"), (
            f"{name} FunctionName is not a template variable: {fn!r}"
        )
        assert "arn:aws:lambda" not in fn


def test_manual_intervention_handler_is_reachable(machine):
    states = machine["States"]
    # A HandleFailure task must exist and be reached by at least one catch.
    assert "HandleFailure" in states
    reached = any(c["Next"] == "HandleFailure" for s in states.values() for c in s.get("Catch", []))
    assert reached, "HandleFailure is not reachable from any catch path"


def test_every_task_has_a_documented_contract(machine):
    contracts = (ASL_DIR.parent / "docs" / "workflow-task-contracts.md").read_text()
    for name in _tasks(machine):
        assert name in contracts, f"task {name} is not documented in workflow-task-contracts.md"
