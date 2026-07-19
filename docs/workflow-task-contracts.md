# Workflow Task Contracts

Every Step Functions task's contract. Task Lambda ARNs are **not** hard-coded in
the ASL; they are injected via Terraform variables (`var.task_function_arns`) and
rendered with `templatefile()`, so no real ARN is embedded in the versioned
definition (enforced by `test_asl_contract.py::test_no_raw_placeholder_arns`).

**Not live-tested:** AWS Organizations account creation (`CreateAccount`) and
account closure (`CloseAccount`) have never been executed against a real AWS
Organization. See [production-boundaries.md](production-boundaries.md).

## Implementation status legend

- **Local** — logic exists and is exercised by the Python simulator/tests.
- **Adapter** — a thin deployable Lambda adapter is defined/straightforward.
- **Simulated** — represented only by the simulator; no Lambda body.
- **Prod-AWS** — requires real AWS Organizations/Cost integration to run.

## Provisioning tasks

| Task | Input | Output | Retryable errors | Permanent errors | Timeout | Idempotency | IAM (least-priv) | Compensation | Status |
|------|-------|--------|------------------|------------------|---------|-------------|------------------|--------------|--------|
| CreateAccount | `request_id, account_name, owner, environment` | `aws_account_id` | `RetryableStepError`, `ThrottlingException`, `TooManyRequestsException` | quota/validation | 3600s | Execution name = `provision-<request_id>`; account id persisted mid-flight so re-drive skips creation | `organizations:CreateAccount`, `organizations:DescribeCreateAccountStatus` | none (account left in quarantine OU on later failure) | Simulated / Prod-AWS |
| ApplyBaseline | `aws_account_id, regions` | baseline result | `RetryableStepError`, `ThrottlingException` | SCP denial | 900s | Idempotent config apply (declarative) | `cloudformation:*` (StackSets), `config:PutConfigRule`, `guardduty:*` scoped | re-run apply | Simulated / Prod-AWS |
| AssignIdentity | `aws_account_id, owner, environment` | identity result | — | permission-set errors | 900s | Idempotent assignment (create-if-absent) | `sso:CreateAccountAssignment`, `sso:DescribeAccountAssignmentCreationStatus` | remove assignment | Simulated / Prod-AWS |
| CreateBudget | `aws_account_id, monthly_budget, owner` | budget result | `RetryableStepError`, `ThrottlingException` | invalid budget | 900s | Idempotent on budget name | `budgets:CreateBudget`, `ce:CreateAnomalyMonitor` | delete budget | Adapter |
| MarkCompleted | `request_id, aws_account_id` | — | — | repo/version errors | 900s | Conditional write on `Version` | `dynamodb:PutItem` on the requests table | none | Local |
| NotifyOwner | `request_id, owner, aws_account_id` | — | `States.ALL` (bounded ×2) | — (catches to success) | 900s | At-least-once notification | `sns:Publish` to the owner topic | none (best-effort) | Adapter |
| HandleFailure | `request_id, error` | — | — | — | 900s | Idempotent state write | `dynamodb:PutItem` | records `FAILED`/`ACTION_REQUIRED` | Local |

## Decommissioning tasks

| Task | Input | Output | Retryable errors | Permanent errors | Timeout | Idempotency | IAM (least-priv) | Compensation | Status |
|------|-------|--------|------------------|------------------|---------|-------------|------------------|--------------|--------|
| DiscoverDependencies | `aws_account_id` | dependency findings | `RetryableStepError`, `ThrottlingException` | — | 900s | Read-only, idempotent | `config:*Get*`, `tag:GetResources` | none | Simulated / Prod-AWS |
| CheckCommitments | `aws_account_id` | commitment findings | — | — | 900s | Read-only, idempotent | `ce:GetReservationUtilization`, `savingsplans:Describe*` | none | Simulated / Prod-AWS |
| ExportEvidence | `aws_account_id, dependencies, commitments` | `EvidenceExport` | `States.ALL` (bounded) | write failure | 900s | Object key = `<decommission_id>.json` | `s3:PutObject` to the evidence bucket | none | Adapter |
| QuarantineAccount | `aws_account_id, request_id` | quarantine record | `States.ALL` (bounded) | OU move denial | 900s | Idempotent OU move | `organizations:MoveAccount` | move back to source OU | Simulated / Prod-AWS |
| CloseAccount | `aws_account_id, request_id` | — | `States.ALL` (bounded) | closure limits | 900s | Idempotent (already-closed is success) | `organizations:CloseAccount` | none (irreversible) | Simulated / Prod-AWS |
| HandleFailure | `request_id, error` | — | — | — | 900s | Idempotent state write | `dynamodb:PutItem` | records `DECOMMISSION_FAILED` | Local |

## Retryability & backoff

Transient errors raise `RetryableStepError` and are retried with explicit
exponential backoff (`IntervalSeconds` + `BackoffRate`, bounded `MaxAttempts`).
Permanent errors raise `WorkflowStepError` and route straight to the catch path;
they are never retried. This split maps to the failure taxonomy
(`RETRYABLE_DEPENDENCY_FAILURE` vs. `PERMANENT_DEPENDENCY_FAILURE`).

## Approval / wait expiry

The decommissioning `WaitQuarantinePeriod` state is a bounded `Wait` (finite
`Seconds`). Human-approval gates are modelled in the service layer with an
approval TTL (`ControlPlaneConfig.approval_ttl_minutes`); an expired approval
transitions to `CANCELLED` (`APPROVAL_TIMEOUT`). No wait can remain open forever.

## Correlation

`request_id` (provisioning) / `correlation_id` (decommissioning) is threaded
through task payloads and every audit event, so a single execution is traceable
end to end (`test_asl_contract.py::test_correlation_identifier_propagates`).
