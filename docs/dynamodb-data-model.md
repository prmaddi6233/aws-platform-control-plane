# DynamoDB Data Model

Single-table design for account requests, idempotency guards, and the audit
trail. One table keeps related items co-located and lets a request and its audit
history be read with a single `Query`.

| Entity          | PK                      | SK                          | Notes |
|-----------------|-------------------------|-----------------------------|-------|
| Request metadata| `REQ#<request_id>`      | `METADATA`                  | Holds serialized state + `Version` |
| Idempotency guard| `IDEMPOTENCY#<key>`    | `ACCOUNT_REQUEST`           | Written in the same transaction as the request |
| Audit event     | `REQ#<request_id>`      | `AUDIT#<iso8601>#<audit_id>`| Append-only; sorts chronologically |

## Attributes (request metadata item)

| Attribute        | Type | Purpose |
|------------------|------|---------|
| `Data`           | S    | JSON snapshot of the `AccountRequest` domain entity |
| `Version`        | N    | Optimistic-concurrency counter |
| `State`          | S    | Denormalized lifecycle state for filtering |
| `IdempotencyKey` | S    | Denormalized for traceability |
| `Type`           | S    | `AccountRequest` (enables type filtering) |

## Access patterns

| # | Need | Operation |
|---|------|-----------|
| 1 | Create request idempotently | `TransactWriteItems`: put request (`attribute_not_exists(PK)`) + put idempotency guard (`attribute_not_exists(PK)`) |
| 2 | Resolve idempotency key | `GetItem` on `IDEMPOTENCY#<key>` → `RequestId` → pattern 3 |
| 3 | Read request + version | `GetItem` on `REQ#<id> / METADATA` |
| 4 | Update state with concurrency safety | `PutItem` with `ConditionExpression = "attribute_exists(PK) AND Version = :v"` |
| 5 | Read audit trail | `Query` `PK = REQ#<id> AND begins_with(SK, "AUDIT#")` |
| 6 | List all requests (operator) | `Scan` filtered on `Type = AccountRequest` — acceptable for ops, not hot paths |

## Concurrency

Writes use optimistic locking. The caller reads `(request, version)`, mutates
the domain entity, and calls `update(request, expected_version)`. The
conditional write fails with `ConditionalCheckFailedException` (surfaced as
`ConcurrencyError`) if another writer advanced the version. There is no
last-writer-wins path.

## Idempotency

`TransactWriteItems` writes the request and the `IDEMPOTENCY#<key>` guard
atomically. A replayed submission fails the guard's condition and is surfaced as
`DuplicateRequestError`; the service layer then compares the payload fingerprint
to distinguish a safe replay (return the original) from a key conflict
(`IdempotencyConflictError`). See [ADR 003](adr/003-idempotency-approach.md).

## TTL

Not enabled on request or audit items — provisioning records are compliance
evidence and are retained. A TTL attribute is appropriate only for ephemeral
lock/lease items, which this model does not use.

## Capacity

`PAY_PER_REQUEST` (on-demand). Account vending is low-throughput and spiky;
on-demand avoids capacity planning and the cost of idle provisioned capacity. At
scale, a GSI on `Type`/`State` would replace the `Scan` in pattern 6.
