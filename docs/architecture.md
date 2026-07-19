# Architecture

## Layers

```
CLI  (src/platform_control_plane/cli.py)
  │        the working local interface; a future API handler is a peer
  ▼
Service  (services/control_plane.py)
  │        orchestrates idempotency, policy, approval, audit — no AWS SDK
  ├── Policy engine (policies/engine.py + rules.py)   → typed decisions
  ├── Domain        (domain/account_request.py)       → enforced state machine
  ├── Audit         (domain/audit.py)                 → immutable events
  ▼
Repository interface (repositories/base.py)
  ├── InMemory  (repositories/memory.py)   → tests + local CLI (file-backed)
  └── DynamoDB  (repositories/account_repository.py) → production, moto-tested
```

The service depends only on the repository **interface**, so the domain never
imports boto3. The same service runs locally (in-memory) and in production
(DynamoDB) by swapping the adapter.

## Lifecycle states

```
SUBMITTED ──policy denied──▶ POLICY_REJECTED (terminal)
    │
    └─policy passed─▶ PENDING_APPROVAL ──reject──▶ REJECTED (terminal)
                         │        │
                         │        └──expiry──▶ CANCELLED (terminal)
                         └─approve─▶ APPROVED ─▶ PROVISIONING
                                                   │
                          ┌────────────────────────┼───────────────────┐
                          ▼                        ▼                    ▼
                      COMPLETED             ACTION_REQUIRED           FAILED
                     (terminal)      (account created, baseline    (hard fail,
                                      incomplete — remediate)      no account)
```

Illegal transitions raise `StateTransitionError`; state is never mutated by
arbitrary string assignment.

## Workflow

Provisioning is orchestrated by a Step Functions state machine
(`statemachines/provisioning.asl.json`) with per-task retries, exponential
backoff, catch handlers routing to a failure path, a DLQ, and best-effort owner
notification. The service's provisioning methods represent the same transitions
locally; the boundary between simulated and real execution is documented in
[production-boundaries.md](production-boundaries.md).

## Workflow failure handling (validated)

Provisioning failure/recovery behaviour is modelled deterministically by
`workflows/simulation.py` and exercised in `tests/unit/test_workflow_simulation.py`:
retryable-then-success, retry exhaustion → DLQ, permanent `CreateAccount` failure
→ `FAILED`, partial baseline failure → `ACTION_REQUIRED`, and idempotent recovery
after interruption. See [failure-recovery.md](failure-recovery.md).

## Decommissioning

A separate aggregate (`domain/decommission.py`) with its own enforced state
machine gates account closure behind deterministic commitment / residual-spend /
dependency checks, separation of duties, and a quarantine retention period. See
[decommissioning.md](decommissioning.md).

## Related documents

- [domain-model / state machine](../src/platform_control_plane/domain/account_request.py)
- [DynamoDB data model](dynamodb-data-model.md)
- [Threat model](threat-model.md) · [SLOs](slos.md) · [Failure modes](failure-modes.md) · [Failure recovery](failure-recovery.md)
- ADRs: [001](adr/001-step-functions-over-ecs.md) · [002](adr/002-dynamodb-state-store.md) · [003](adr/003-idempotency-approach.md) · [004](adr/004-cli-over-api-for-mvp.md)
