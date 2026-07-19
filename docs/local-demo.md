# Local Demo

Drive the full account-request lifecycle locally, with no AWS account. State is
kept in a JSON file so each command sees the previous one's result.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Walkthrough

```bash
# 1. Submit a request (idempotency key is optional; one is generated if omitted).
control-plane submit --file examples/request.json --idempotency-key demo-key-0001
#  → state: PENDING_APPROVAL   (passed schema + policy, awaiting approval)

# 2. List requests and capture the id.
control-plane list
RID=$(control-plane list | awk '{print $1}')

# 3. Inspect status.
control-plane status "$RID"

# 4. Approve it.
control-plane approve "$RID" --approver alice@example.com
#  → state: APPROVED

# 5. Read the immutable audit trail.
control-plane audit "$RID"
#  REQUEST_SUBMITTED / POLICY_EVALUATED / APPROVAL_GRANTED

# 6. Idempotent replay — same key + same payload returns the ORIGINAL request.
control-plane submit --file examples/request.json --idempotency-key demo-key-0001
```

## Policy rejection

A request in an unapproved region is persisted in `POLICY_REJECTED` for
auditability rather than silently dropped:

```bash
# Edit examples/request.json to use regions: ["ap-southeast-2"], then:
control-plane submit --file examples/request.json --idempotency-key demo-key-0002
control-plane status <id>   # → state: POLICY_REJECTED
```

## Notes

- Logs are structured JSON on **stderr**; machine-readable output is on stdout.
- The default store is `./.control-plane-state.json` (override with `--store`).
- The same `ControlPlaneService` runs against DynamoDB in production by swapping
  the in-memory repository for `AccountRequestRepository`.
