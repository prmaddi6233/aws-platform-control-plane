"""Local CLI for the AWS Platform Control Plane.

A dependency-free (argparse) interface backed by the file-persisting in-memory
repository, so the full request lifecycle can be demonstrated locally without an
AWS account:

    control-plane submit --file examples/request.yaml
    control-plane status <request_id>
    control-plane approve <request_id> --approver alice@example.com
    control-plane audit <request_id>

State is stored in a JSON file (default: ./.control-plane-state.json).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from platform_control_plane.api.schemas import CreateAccountRequest
from platform_control_plane.exceptions import PlatformError
from platform_control_plane.repositories.memory import InMemoryAccountRequestRepository
from platform_control_plane.services.control_plane import ControlPlaneService

DEFAULT_STORE = ".control-plane-state.json"


def _load_request_file(path: Path) -> dict[str, Any]:
    """Load a request from JSON or YAML, supporting a nested ``account:`` block."""
    text = path.read_text()
    data: dict[str, Any]
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise SystemExit(
                "PyYAML is required to read YAML files. Use a .json file or `pip install pyyaml`."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    account = data.get("account", data)
    # The example schema uses owner_email for the contact; the API field is `owner`.
    owner = account.get("owner_email") or account.get("owner")
    return {
        "account_name": account.get("name") or account.get("account_name"),
        "owner": owner,
        "environment": account.get("environment"),
        "data_classification": account.get("data_classification"),
        "monthly_budget": account.get("monthly_budget"),
        "regions": account.get("regions", []),
    }


def _service(store: str) -> ControlPlaneService:
    repo = InMemoryAccountRequestRepository(persist_path=store)
    return ControlPlaneService(repo)


def _print_request(request: Any) -> None:
    print(
        json.dumps(
            {
                "request_id": request.request_id,
                "account_name": request.account_name,
                "state": request.state.value,
                "owner": request.owner,
                "environment": request.environment,
                "aws_account_id": request.aws_account_id,
                "error": request.provisioning_error,
            },
            indent=2,
        )
    )


def cmd_submit(args: argparse.Namespace) -> int:
    fields = _load_request_file(Path(args.file))
    idempotency_key = args.idempotency_key or f"cli-{uuid.uuid4().hex}"
    try:
        create = CreateAccountRequest(idempotency_key=idempotency_key, **fields)
    except PydanticValidationError as exc:
        print(f"Invalid request: {exc}", file=sys.stderr)
        return 2
    request = _service(args.store).submit(create, actor=args.actor)
    print(f"Submitted with idempotency key: {idempotency_key}")
    _print_request(request)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _print_request(_service(args.store).get(args.request_id))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for request in _service(args.store).list_requests():
        print(f"{request.request_id}  {request.state.value:<16}  {request.account_name}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    request = _service(args.store).approve(args.request_id, approver=args.approver)
    _print_request(request)
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    request = _service(args.store).reject(
        args.request_id, approver=args.approver, reason=args.reason
    )
    _print_request(request)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    for event in _service(args.store).audit_trail(args.request_id):
        print(f"{event.occurred_at.isoformat()}  {event.action.value:<24}  {event.actor}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """Run a workflow scenario and print its canonical JSON result.

    Exit codes: 0 = expected success, 2 = expected business/policy block,
    3 = validation/usage error, 4 = unexpected platform failure.
    """
    from platform_control_plane.workflows.scenarios import run_scenario

    try:
        result = run_scenario(args.workflow_type, args.scenario)
    except ValueError as exc:
        print(json.dumps({"error": "USAGE_ERROR", "message": str(exc)}), file=sys.stderr)
        return 3
    except PlatformError as exc:  # unexpected platform failure surfaced structurally
        print(json.dumps({"error": exc.code, "message": str(exc)}), file=sys.stderr)
        return 4
    print(result.model_dump_json(indent=2))
    return result.exit_code()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-plane", description=__doc__)
    parser.add_argument("--store", default=DEFAULT_STORE, help="Path to the state file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sim = sub.add_parser("simulate", help="Run a workflow scenario (machine-readable JSON)")
    # Validated in run_scenario so an unknown type yields exit 3, not argparse's 2.
    p_sim.add_argument("workflow_type", help="provisioning | decommissioning")
    p_sim.add_argument("--scenario", required=True)
    p_sim.set_defaults(func=cmd_simulate)

    p_submit = sub.add_parser("submit", help="Submit an account request")
    p_submit.add_argument("--file", required=True, help="JSON/YAML request file")
    p_submit.add_argument("--idempotency-key", help="Client idempotency key")
    p_submit.add_argument("--actor", default="cli-user")
    p_submit.set_defaults(func=cmd_submit)

    p_status = sub.add_parser("status", help="Show a request's status")
    p_status.add_argument("request_id")
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", help="List all requests")
    p_list.set_defaults(func=cmd_list)

    p_approve = sub.add_parser("approve", help="Approve a request")
    p_approve.add_argument("request_id")
    p_approve.add_argument("--approver", required=True)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="Reject a request")
    p_reject.add_argument("request_id")
    p_reject.add_argument("--approver", required=True)
    p_reject.add_argument("--reason", required=True)
    p_reject.set_defaults(func=cmd_reject)

    p_audit = sub.add_parser("audit", help="Show a request's audit trail")
    p_audit.add_argument("request_id")
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PlatformError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
