# Platform Service Level Objectives (SLOs)

We treat the control plane as a production service. These SLOs define what "healthy" means for account vending.

## 1. Availability / Workflow Completion

**SLI:** The percentage of valid `AccountRequest` executions that reach either the `COMPLETED` or `ACTION_REQUIRED` state without terminating in a `FAILED` state.
**SLO:** 99.5% over a 30-day rolling window.

*Rationale:* Account provisioning involves multiple cross-account IAM operations that can be eventually consistent. We rely on Step Functions retries to mask transient AWS API errors. A failure means the retries were exhausted and human intervention is needed.

## 2. Latency / Vending Speed

**SLI:** The duration from an `AccountRequestApproved` event to the `AccountProvisioningCompleted` event.
**SLO:** 95% of accounts are fully provisioned and baselined within **30 minutes**.

*Rationale:* Creating an AWS account takes seconds, but applying a full security baseline (Config rules, GuardDuty enablement, CloudTrail trails) across multiple regions takes time. 30 minutes is the upper bound for our infrastructure-as-code automation to settle.

## 3. Correctness

**SLI:** The percentage of provisioned accounts that pass the automated continuous compliance scan (e.g., AWS Security Hub / custom Config rules) within 1 hour of creation.
**SLO:** 100%.

*Rationale:* An account is not considered "vended" until it is secure. If an account is handed over without the baseline applied, the platform has failed. We do not tolerate a margin of error for security baselines.
