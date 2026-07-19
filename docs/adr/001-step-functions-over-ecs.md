# Architecture Decision Record
# 001 - Step Functions over ECS for Workflow Orchestration

## Status
Accepted

## Context
Account provisioning and decommissioning are multi-step workflows. They involve interacting with AWS Organizations to create the account, assuming roles to apply baselines (which may take minutes to propagate), assigning identities, and setting up budgets.

We need a system to orchestrate these steps that provides:
1. Retries with exponential backoff (e.g., waiting for cross-account IAM roles to propagate).
2. Long-running waits (e.g., a 7-day quarantine period during decommissioning).
3. State persistence.
4. Clear visibility into failure points.
5. Error handling and DLQ routing.

Options considered:
- **AWS Step Functions (Standard or Express)**
- **Amazon ECS Tasks running a workflow engine (e.g., Temporal or custom Python scripts)**
- **EventBridge Choreography (Event-driven chained Lambdas)**

## Decision
We will use **AWS Step Functions (Standard Workflows)**.

## Rationale
- **Wait States:** Decommissioning requires a 7-day quarantine wait. Standard Step Functions support executions up to 1 year, making `Wait` states trivial. ECS or EventBridge would require external state tracking (e.g., DynamoDB + EventBridge Scheduler) for long delays.
- **Built-in Retries and Catch:** ASL allows declarative retries on specific exception types (e.g., `ThrottlingException`) with backoff, and a global `Catch` to a failure handler.
- **No Idle Compute:** Step Functions charge per state transition. An ECS task blocking on an API call or sleeping incurs compute costs.
- **Observability:** Step Functions provides visual graphs of execution history, which is invaluable when debugging why an account failed to provision.
- **Choreography vs Orchestration:** While EventBridge choreography is looser coupled, account lifecycle is a strict, sequential business process. Centralized orchestration via Step Functions is easier to reason about, test, and monitor than a distributed chain of events.

## Consequences
- **Positive:** We gain robust retry logic without writing custom looping code in Lambda.
- **Positive:** The 7-day quarantine wait is handled natively by the platform.
- **Negative:** ASL (Amazon States Language) JSON is verbose and requires tooling (or Terraform) to manage effectively.
- **Negative:** We must be careful not to hit Step Functions payload size limits (256KB); large payloads must be passed via S3, though account metadata easily fits in the limit.
