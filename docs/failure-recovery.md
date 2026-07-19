# Failure Recovery

How the control plane recovers from provisioning failures. This complements
[failure-modes.md](failure-modes.md) (what fails) with the operator procedures
(how to recover). The provisioning workflow's failure handling is modelled and
tested deterministically by `workflows/simulation.py` and
`tests/unit/test_workflow_simulation.py`.

## Terminal outcomes

| Outcome | Meaning | Recovery |
|---------|---------|----------|
| `COMPLETED` | All steps succeeded. | None. |
| `ACTION_REQUIRED` | Account **was created**, a later step (baseline/identity/budget) failed permanently or exhausted retries. | Fix the root cause (e.g. SCP), then re-drive the workflow — see *Re-drive* below. Idempotent: the account is not re-created. |
| `FAILED` | A step failed **before** the account existed (e.g. `CreateAccount` quota). | No account to clean up. Fix the cause and resubmit (new idempotency key or the same key with the same payload). |

The distinction is enforced in the domain: `mark_action_required` requires an
`aws_account_id`; `mark_failed` does not. The simulator picks the outcome based
on whether the account existed at the point of failure.

## Retry and DLQ

- Each task retries transient errors (`RetryableStepError`, throttling) with
  bounded attempts and exponential backoff (see `provisioning.asl.json`).
- On permanent failure or exhausted retries, the workflow routes to
  `HandleFailure`; the failed event is captured on the **workflow DLQ**
  (`aws_sqs_queue.workflow_dlq`, 14-day retention).
- The `dlq-not-empty` CloudWatch alarm pages when messages are present.

### DLQ redrive procedure

1. Alarm `…-dlq-not-empty` fires. Inspect the message: `{step, request_id, error}`.
2. Determine the root cause (SCP, quota, IAM propagation delay, dependency).
3. Remediate the underlying cause.
4. Re-drive: re-invoke the provisioning workflow for `request_id`. Because the
   account id is persisted mid-workflow (`record_account_created`), a re-drive
   **resumes** — `CreateAccount` is skipped when an account already exists.
5. Confirm the request reaches `COMPLETED`; delete the DLQ message.

## Recovery after interruption

If the workflow process dies mid-execution, the request is left in
`PROVISIONING` with the account id already persisted. Re-entry is idempotent:

- `ProvisioningSimulator.run` (and the real workflow) does not re-`start` an
  already-`PROVISIONING` request.
- `CreateAccount` is skipped when `aws_account_id` is set.

This is covered by `test_recovery_after_interruption_skips_account_creation`.

## SLO linkage

A `FAILED` execution consumes error budget and triggers the
`…-provisioning-failures` alarm. `ACTION_REQUIRED` counts toward the SLO's
success bucket (the request reached a defined, actionable terminal state within
the window) — see [slos.md](slos.md).
