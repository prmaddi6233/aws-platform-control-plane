# Failure Modes Analysis

Distributed workflows fail. This document outlines how the control plane responds to specific failure conditions.

## 1. Concurrent Request Updates
**Scenario:** A user submits an approval just as a background cleanup job tries to reject a stale request.
**Response:** DynamoDB optimistic locking (`Version` attribute). One transaction will succeed; the other will receive a `ConditionalCheckFailedException` (mapped to `ConcurrencyError`) and the caller will be forced to retry against the new state.

## 2. Duplicate Submission (Idempotency)
**Scenario:** A CI pipeline experiences a network timeout and retries the `POST /accounts` request.
**Response:** The API requires an `idempotency_key`. The DynamoDB `TransactWriteItems` call attempts to write an `IDEMPOTENCY#<key>` record. The second request will fail the transaction, catching `DuplicateRequestError`, and the API will return a `409 Conflict` (or `200 OK` with the existing request details, depending on API contract). No duplicate account is created.

## 3. Transient AWS API Failure
**Scenario:** The AWS Organizations API throttles the `CreateAccount` call during the workflow.
**Response:** The Step Functions state catches `ThrottlingException` and `RetryableStepError`. It applies exponential backoff and retries up to 5 times.

## 4. Partial Provisioning Failure (Poison Pill)
**Scenario:** The account is created, but applying the baseline fails permanently because a required SCP denies the action in that specific OU.
**Response:**
1. Step Functions exhausts retries.
2. The workflow falls back to the `HandleFailure` state.
3. `HandleFailure` marks the domain entity as `FAILED` and records the error message.
4. An alert is sent to the platform team via EventBridge.
5. **No automated rollback is attempted.** Rolling back an AWS account creation is complex (requires closing the account, which has limits). Instead, the account is left in a quarantined OU for a human platform engineer to investigate, fix the SCP, and redrive the workflow.

## 5. Decommissioning with Active Dependencies
**Scenario:** A decommission request is fired, but the account still has an active RDS instance.
**Response:** The `DiscoverDependencies` step in the decommissioning workflow queries AWS Config / Tag Editor. If active infrastructure is found (that isn't part of the baseline), the step throws a non-retryable error, failing the workflow. The account owner is notified to destroy their infrastructure first.
