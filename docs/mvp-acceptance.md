# MVP Acceptance — Evidence Map

Each documented acceptance criterion mapped to the implementation and the test
that proves it. Verified locally: **62 tests pass**; ruff, ruff-format,
mypy(strict), and `terraform validate` are green.

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | Request schema exists | `api/schemas.py` (`CreateAccountRequest`, Pydantic) |
| 2 | Domain model exists | `domain/account_request.py`, `domain/audit.py`, `domain/decommission.py` |
| 3 | State transitions enforced | `AccountRequest` methods raise `StateTransitionError`; `test_account_request.py::test_illegal_state_transition_raises_error` |
| 4 | Idempotency implemented | `services/control_plane.py` (fingerprint) + repos; `test_control_plane_service.py::test_duplicate_identical_submit_returns_original`, `::test_idempotency_key_conflict_on_different_payload` |
| 5 | Local persistence adapter works | `repositories/memory.py`; `test_memory_repository.py` (incl. file persistence) |
| 6 | DynamoDB persistence design exists | `repositories/account_repository.py` + `docs/dynamodb-data-model.md`; `test_account_repository.py` (moto) |
| 7 | ≥1 deterministic policy path works | `policies/engine.py` + `rules.py`; `test_validation.py` (allow + each denial) |
| 8 | Approval path works | `services/control_plane.py::approve/reject`; `test_control_plane_service.py::test_approve_transitions_to_approved`, `::test_reject_transitions_to_rejected`, `::test_approval_after_window_expiry_raises` |
| 9 | Workflow definition exists | `statemachines/provisioning.asl.json`, `decommissioning.asl.json`; rendered via `infrastructure/terraform/stepfunctions.tf` |
| 10 | Retry & failure behaviour documented **and tested** | `docs/failure-recovery.md`; `workflows/simulation.py`; `test_workflow_simulation.py` (retry, exhaustion→DLQ, FAILED, ACTION_REQUIRED, recovery) |
| 11 | CLI local demonstration works | `cli.py`; `test_cli.py` (submit→status→approve→audit); `docs/local-demo.md` |
| 12 | Behavioural tests pass | 62 tests exercising behaviour and failure modes, not imports |
| 13 | Terraform validates | `infrastructure/terraform/*` — `terraform validate` passes |
| 14 | Architecture documentation exists | `docs/architecture.md` |
| 15 | Threat model exists | `docs/threat-model.md` |
| 16 | SLOs exist | `docs/slos.md` (measurable from emitted events; completion event emitted) |
| 17 | CI validates the project | `.github/workflows/validate.yml` (lint, typecheck, test, tf, security) |
| 18 | README claims match implementation | `README.md` — CLI demo + honest Limitations; `docs/production-boundaries.md` |
| 19 | Decommissioning commitment check | `domain/decommission.py::review_commitments`; `test_decommissioning.py::test_orphaned_commitment_blocks_closure` |
| 20 | Residual-spend detection | `domain/decommission.py::check_residual_spend`; `test_decommissioning.py::test_residual_spend_*` |

## Out of scope (integration boundary, documented)

- Live execution against AWS Organizations (task Lambdas are placeholders).
- HTTP API transport (deferred by [ADR 004](adr/004-cli-over-api-for-mvp.md); CLI is the interface).
- Decommissioning CLI surface (service + tests complete; CLI command is a follow-up).

See [production-boundaries.md](production-boundaries.md).
