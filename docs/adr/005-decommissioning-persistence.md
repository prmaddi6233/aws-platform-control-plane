# ADR 005: Separate aggregates & repositories for provisioning vs. decommissioning

- Status: Accepted
- Date: 2026-07-18

## Context

Account provisioning and account decommissioning are both long-running,
audited, idempotent lifecycles, and they share persistence needs (optimistic
concurrency, an idempotency guard, an append-only hash-chained audit trail, an
atomic file store for local demo). It is tempting to collapse them into one
generic repository. But their **domain invariants differ**: a provisioning
request is keyed by a client request and moves through a create/baseline/approve
lifecycle; a decommissioning request is tied to an existing **account**, must
enforce "one active decommission per account," and runs financial pre-closure
gates. Forcing both behind one interface would leak each aggregate's concerns
into the other.

## Decision

- **Two aggregates, two domain-facing repository interfaces.**
  `AbstractAccountRequestRepository` stays focused on account requests.
  `AbstractDecommissioningRequestRepository` is introduced for the
  decommissioning aggregate.
- **Shared infrastructure, not shared interface.** Atomic file persistence
  (`repositories/_filestore.py`), audit sealing + hash-chaining
  (`repositories/_auditlog.py`), optimistic-concurrency versioning, and the
  idempotency pattern are shared code reused by both adapters.
- **One physical DynamoDB table, entity-prefixed keys.** The adapters may share
  the same table because access patterns are compatible and keys are
  namespaced so items cannot collide.

### Single-table key design

| Item | PK | SK | Notes |
|------|----|----|-------|
| Account request metadata | `REQUEST#<request_id>` (`req-…`) | `METADATA` | `Type=AccountRequest`, `Version` |
| Decommission metadata | `REQUEST#<decommission_id>` (`dec-…`) | `METADATA` | `Type=DecommissioningRequest`, `Version` |
| Audit event (either) | `REQUEST#<id>` | `AUDIT#<sequence:012d>#<audit_id>` | append-only, hash-chained |
| Idempotency guard | `IDEMPOTENCY#<key>` | `ACCOUNT_REQUEST` \| `DECOMMISSION_REQUEST` | distinct SK per aggregate |
| Account→decommission index | `ACCOUNT#<account_id>` | `DECOMMISSION#<decommission_id>` | enforces one active decommission per account |

`req-` and `dec-` id prefixes plus the `Type` attribute and distinct
idempotency SKs guarantee provisioning and decommissioning cannot collide even
though both live under `REQUEST#`.

## Query patterns

| Need | Operation |
|------|-----------|
| Create decommission idempotently | `TransactWriteItems`: metadata + `IDEMPOTENCY#…/DECOMMISSION_REQUEST` + `ACCOUNT#…/DECOMMISSION#…`, each `attribute_not_exists` |
| One active decommission per account | conditional write on the `ACCOUNT#…` index item |
| Resolve idempotency key | `GetItem IDEMPOTENCY#<key>/DECOMMISSION_REQUEST` |
| Read state + version | `GetItem REQUEST#<id>/METADATA` |
| Update with concurrency safety | `PutItem` conditional on `Version = :v` |
| Read audit trail | `Query PK=REQUEST#<id> AND begins_with(SK, "AUDIT#")` |

## Concurrency & idempotency

- Optimistic locking via a `Version` counter; stale updates raise `ConcurrencyError`.
- Idempotent create via a client `idempotency_key`; a replay with the same key
  returns the original request, a different payload raises `IdempotencyConflictError`.
- The `ACCOUNT#…` index prevents a second concurrent decommission of the same account.

## Trade-offs & future migration

- **Pro:** aggregates stay cohesive; shared infra avoids copy-paste; one table
  keeps ops simple and enables single-`Query` read of a request + its audit.
- **Con:** a shared table couples deploy lifecycles; a very high-throughput
  future could split decommission into its own table — the separate interface
  makes that a swap of the adapter, not a domain change.
- Audit and idempotency are kept as separate concerns (shared helpers), not
  baked into the lifecycle aggregates.
