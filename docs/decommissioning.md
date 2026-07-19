# Decommissioning

Closing an AWS account is high-consequence and effectively irreversible. The
decommissioning flow gates closure behind deterministic checks and a quarantine
retention period, and enforces separation of duties.

## Lifecycle

```
REQUESTED ──authorize (authorizer ≠ requester)──▶ AUTHORIZED
                                                     │
             ┌───────────────────────────────────────┤ run pre-closure gates
             ▼ (any gate fails)                       ▼ (all gates pass)
          BLOCKED                              EVIDENCE_EXPORTED
       (needs remediation)                            │
                                                       ▼ quarantine(now + retention)
                                                 QUARANTINED
                                                       │ retention elapsed
                                                       ▼
                                                    CLOSED
```

`FAILED` is reachable from any non-terminal state on hard error. Illegal
transitions raise `StateTransitionError`.

## Pre-closure gates (deterministic, no AWS, no LLM)

| Gate | Blocks closure when | Evidence |
|------|---------------------|----------|
| **Commitment review** (`review_commitments`) | A non-cancellable RI/Savings Plan whose term ends in the future would be orphaned | orphaned commitment ids + `total_monthly_waste` |
| **Residual spend** (`check_residual_spend`) | Trailing-window average daily spend exceeds the closure threshold | observed window + average + threshold |
| **Dependency check** | Active non-baseline resources remain | resource ids |

Each gate contributes a human-readable reason to `blocking_reasons`. The trailing
window (not the whole month) is used for residual spend so an early-month spike
neither masks nor fabricates current residual spend.

## Separation of duties

`authorize()` rejects an authorizer equal to the requester. A decommission
cannot be self-approved.

## Retention

After all gates pass, the account is quarantined until
`created_at + quarantine_retention` (default 7 days, configurable). `close()`
refuses to run before the retention period elapses, even if called directly.

## Configuration

`ControlPlaneConfig`: `quarantine_retention_days`, `residual_lookback_days`,
`residual_threshold_daily`.

## Tests

`tests/unit/test_decommissioning.py` covers each gate (block and pass), the
happy path through quarantine to closure, separation of duties, and the illegal
transitions. The workflow ordering mirrors `statemachines/decommissioning.asl.json`.
