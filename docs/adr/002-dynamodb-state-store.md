# Architecture Decision Record
# 002 - DynamoDB with Optimistic Locking for State Storage

## Status
Accepted

## Context
The Control Plane exposes an API (e.g., via API Gateway / FastAPI) for submitting account requests. We need a persistence layer to store the `AccountRequest` entity and track its state (SUBMITTED -> APPROVED -> PROVISIONING -> COMPLETED).

Requirements:
1. **Idempotency:** If the same request (e.g., triggered by a CI pipeline retry) is submitted twice, the system must not create two accounts.
2. **Concurrency Control:** When a workflow step updates the state of a request, it must not overwrite a more recent update made by a user or another process.
3. **Low Latency / Serverless:** The database should scale to zero and require no connection pooling overhead for Lambda/FastAPI.

Options considered:
- **Amazon DynamoDB**
- **Amazon Aurora Serverless v2 (PostgreSQL)**

## Decision
We will use **Amazon DynamoDB** with a single-table design, implementing **Optimistic Locking** via a `Version` attribute, and enforcing idempotency using `TransactWriteItems`.

## Rationale
- **Idempotency via Transactions:** We can insert the main `REQ#<id>` item and an `IDEMPOTENCY#<key>` item in a single transaction. If the idempotency key already exists, the transaction fails entirely, preventing duplicate account creations.
- **Concurrency via Conditional Updates:** By incrementing a `Version` attribute on every write and using `ConditionExpression="Version = :expected"`, we implement optimistic locking. If two processes try to update the request simultaneously, one will get a `ConditionalCheckFailedException` and can be handled (or retried) explicitly.
- **No Connection Pools:** DynamoDB uses HTTP APIs, completely avoiding the connection exhaustion issues common when Lambda scales rapidly against relational databases.
- **Cost:** For a control plane with relatively low write throughput (account creations are infrequent compared to application traffic), DynamoDB on-demand pricing is pennies per month.

## Consequences
- **Positive:** Guaranteed idempotency at the database layer.
- **Positive:** Safe concurrent updates without pessimistic locks.
- **Negative:** The data model is NoSQL. If we need complex relational queries (e.g., "show all accounts for team X that cost more than Y"), we will need to stream changes to a secondary analytical store (like Athena) or use Global Secondary Indexes (GSIs). For the control plane's transactional needs, this is acceptable.
