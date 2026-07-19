# Production Boundaries

This repository is an **evaluatable reference implementation**. It draws a hard
line between what is fully implemented and tested, and what is a documented
interface awaiting real AWS integration. Nothing here creates or closes a real
AWS account.

## Fully implemented and tested (no AWS account needed)

- Typed request schema and domain model with an enforced state machine.
- Idempotent submission (identical replay vs. key conflict) and optimistic
  concurrency, tested against both the in-memory and moto-mocked DynamoDB
  adapters.
- Deterministic policy engine returning traceable decisions.
- Approval, rejection, and approval-expiry with a full audit trail.
- Local CLI driving the entire lifecycle over a file-backed store.

## Deployable infrastructure (validates; not deployed here)

- Terraform for DynamoDB, KMS, SQS DLQ, the Step Functions state machine, its
  least-privilege IAM role, and CloudWatch alarms. `terraform validate` passes;
  no real backend is required to evaluate it.

## Simulated / interface-only (the integration boundary)

| Concern | Status |
|---------|--------|
| AWS Organizations `CreateAccount` | Represented by the `CreateAccount` workflow task (a placeholder Lambda ARN). Not implemented. |
| Security baseline, IAM Identity Center, Budgets | Workflow tasks with defined inputs; Lambda bodies not implemented. |
| Owner notification | Best-effort workflow task; not implemented. |
| API transport (API Gateway + OIDC) | Documented in the threat model; the domain/policy/service layers are complete, but no HTTP entrypoint exists. The CLI is the working interface. |
| Live execution of the state machine | The ASL validates and renders via Terraform, but has never run against real Lambdas. |

## Safety

The account-mutating task Lambdas are intentionally absent. The Terraform takes
their ARNs as **inputs** (defaulted to clearly-labelled placeholders) and never
creates account-provisioning logic. Any real deployment must supply audited
Lambda implementations and would gate account creation behind explicit,
reviewed opt-in.
