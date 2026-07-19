# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Decommissioning domain (`domain/decommission.py`) with an enforced state
  machine, separation-of-duties authorization, and a quarantine retention gate
- Deterministic pre-closure checks: commitment review (orphaned RI/Savings Plan)
  and residual-spend detection, with traceable evidence
- `DecommissioningService` orchestrating authorize → gates → quarantine → close
- Deterministic provisioning workflow simulator (`workflows/simulation.py`)
  covering retry, retry exhaustion → DLQ, permanent failure → FAILED, partial
  baseline failure → ACTION_REQUIRED, and idempotent recovery after interruption
- `ControlPlaneService.record_account_created` / `require_action` /
  `fail_provisioning`; domain `record_account_created` for resumable workflows
- Docs: failure-recovery, decommissioning
- Local `control-plane` CLI: submit / status / list / approve / reject / audit,
  backed by a file-persisting store (no AWS account required)
- `ControlPlaneService` orchestrating idempotency, policy, approval (with
  expiry), provisioning transitions, and an immutable audit trail
- Structured policy engine (`policies/engine.py` + `rules.py`) returning typed
  `PolicyDecision`s with `policy_id` / reason / remediation
- Repository interface (`repositories/base.py`) with in-memory and DynamoDB
  adapters; DynamoDB adapter extended with idempotency lookup, listing, and audit
- `AuditEvent` domain model; `POLICY_REJECTED` and `CANCELLED` lifecycle states
- `IdempotencyConflictError` (key reused with a different payload) and payload
  fingerprinting to separate safe replays from conflicts
- Terraform for KMS, SQS DLQ, Step Functions state machine, least-privilege IAM
  role, and CloudWatch alarms (in addition to DynamoDB)
- Docs: architecture, DynamoDB data model, production boundaries, local demo,
  ADR 003 (idempotency) and ADR 004 (CLI-over-API)
- Structured logs redirected to stderr so CLI stdout stays machine-readable
- Initial project structure and domain model
- Account request schema with Pydantic validation
- Account lifecycle state machine (SUBMITTED → COMPLETED / ACTION_REQUIRED / FAILED)
- Provisioning and decommissioning workflow orchestration
- DynamoDB persistence abstraction with idempotency support
- Policy validation engine (budget, region, naming, data classification)
- Step Functions ASL definitions for both workflows
- Structured JSON logging with correlation IDs
- Explicit exception hierarchy
- Unit tests for domain model, validation, and state transitions
- Repository tests (moto DynamoDB) for idempotency and optimistic concurrency
- `AccountProvisioningCompleted` event emission so the latency SLI is measurable
- Architecture documentation, ADRs, threat model, SLOs
- Terraform infrastructure definitions
- GitHub Actions CI pipeline
