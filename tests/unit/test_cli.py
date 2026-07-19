"""End-to-end CLI tests against the file-backed store."""

import json

from platform_control_plane.cli import main


def _write_request(path, **overrides):
    account = {
        "name": "example-payments-production",
        "owner_email": "payments@example.com",
        "environment": "production",
        "data_classification": "confidential",
        "monthly_budget": 5000,
        "regions": ["ca-central-1"],
    }
    account.update(overrides)
    path.write_text(json.dumps({"schema_version": "1.0", "account": account}))


def _submit(tmp_path, store, key, capsys, **overrides):
    req_file = tmp_path / "req.json"
    _write_request(req_file, **overrides)
    rc = main(["--store", str(store), "submit", "--file", str(req_file), "--idempotency-key", key])
    out = capsys.readouterr().out
    assert rc == 0
    request_id = json.loads(out.split("\n", 1)[1])["request_id"]
    return request_id


def test_cli_submit_status_approve_audit(tmp_path, capsys):
    store = tmp_path / "state.json"
    request_id = _submit(tmp_path, store, "cli-key-0001", capsys)

    rc = main(["--store", str(store), "status", request_id])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["state"] == "PENDING_APPROVAL"

    rc = main(["--store", str(store), "approve", request_id, "--approver", "alice@example.com"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["state"] == "APPROVED"

    rc = main(["--store", str(store), "audit", request_id])
    assert rc == 0
    assert "APPROVAL_GRANTED" in capsys.readouterr().out


def test_cli_policy_rejected_request(tmp_path, capsys):
    store = tmp_path / "state.json"
    request_id = _submit(tmp_path, store, "cli-key-0002", capsys, regions=["ap-southeast-2"])
    main(["--store", str(store), "status", request_id])
    assert json.loads(capsys.readouterr().out)["state"] == "POLICY_REJECTED"


def test_cli_idempotent_resubmit_returns_same_id(tmp_path, capsys):
    store = tmp_path / "state.json"
    first = _submit(tmp_path, store, "cli-key-0003", capsys)
    second = _submit(tmp_path, store, "cli-key-0003", capsys)
    assert first == second


def test_cli_list_shows_request(tmp_path, capsys):
    store = tmp_path / "state.json"
    request_id = _submit(tmp_path, store, "cli-key-0004", capsys)
    main(["--store", str(store), "list"])
    assert request_id in capsys.readouterr().out
