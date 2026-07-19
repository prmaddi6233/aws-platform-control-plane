# Failure Taxonomy

A single authoritative registry (`domain/failures.py`) classifies every failure
the control plane can produce. Domain exceptions, the provisioning and
decommissioning services, the CLI, the workflow simulator, audit events, and the
Step Functions catch paths all reference it, so classification never drifts
between layers. Tests in `test_failure_taxonomy.py` assert the registry is
complete and internally consistent.

## Exit-code convention (CLI / simulator)

| Code | Meaning |
|------|---------|
| 0 | Completed with the expected successful result |
| 2 | Expected business or policy block |
| 3 | Validation or usage error |
| 4 | Unexpected / unrecoverable platform failure |

Raw stack traces are never emitted in normal CLI JSON output; errors are surfaced
as structured `{"error": <code>, "message": …}` on stderr.

## Classifications

| Class | Retryable | Correctable by | Alert | Exit | Typical terminal / intervention state | Recommended action |
|-------|-----------|----------------|-------|------|----------------------------------------|--------------------|
| VALIDATION_ERROR | no | user | NONE | 3 | rejected at API (no workflow) | Fix the request payload and resubmit. |
| POLICY_DENIED | no | user | NONE | 2 | `POLICY_REJECTED` | Satisfy the failed policy, then resubmit. |
| AUTHORIZATION_ERROR | no | operator | INFO | 2 | stays pre-validation | Obtain approval from a distinct approver. |
| IDEMPOTENCY_CONFLICT | no | user | NONE | 2 | rejected (create) | New key, or resend the identical payload. |
| CONCURRENCY_CONFLICT | yes | platform | INFO | 4 | re-read + retry | Re-read current state and retry. |
| APPROVAL_TIMEOUT | no | user | INFO | 2 | `CANCELLED` | Resubmit; the approval window elapsed. |
| RETRYABLE_DEPENDENCY_FAILURE | yes | platform | WARNING | 4 | retried; `ACTION_REQUIRED` if exhausted | Allow bounded retries; investigate on exhaustion. |
| PERMANENT_DEPENDENCY_FAILURE | no | platform | PAGE | 4 | `FAILED` / `ACTION_REQUIRED` / `DECOMMISSION_FAILED` | Remediate root cause, then re-drive. |
| COMMITMENT_BLOCK | no | user | WARNING | 2 | `DECOMMISSION_ACTION_REQUIRED` | Reassign/modify or explicitly accept the commitment. |
| RESIDUAL_SPEND_BLOCK | no | user | WARNING | 2 | `DECOMMISSION_ACTION_REQUIRED` | Disable remaining workloads until spend drops. |
| WORKFLOW_TIMEOUT | no | platform | PAGE | 4 | manual intervention | Inspect the stalled execution; route to manual. |
| MANUAL_INTERVENTION_REQUIRED | no | platform | PAGE | 4 | `ACTION_REQUIRED` | Platform engineer resolves and re-drives per runbook. |

## Where it is wired

- **Domain exceptions** — every `PlatformError` subclass sets `failure_class`.
- **Decommissioning domain** — `block()` sets `COMMITMENT_BLOCK` or
  `RESIDUAL_SPEND_BLOCK`; hard failures set `PERMANENT_DEPENDENCY_FAILURE`.
- **Simulator / CLI** — `ScenarioResult.exit_code()` derives the exit code from
  the failure class; the `failure` block carries the full metadata.
- **Step Functions** — catch paths route retryable vs. permanent errors per the
  `RetryableStepError` / `WorkflowStepError` split (see workflow-task-contracts.md).
- **Docs** — this file is the human-readable projection of the registry.
