# ADR 003: Idempotency via key + payload fingerprint

- Status: Accepted
- Date: 2026-07-17

## Context

Account creation is expensive and irreversible-ish (closing an AWS account has
limits and delays). Clients (CI pipelines, retries behind timeouts) will resend
the same request. We must guarantee at-most-once account creation per logical
request, while still returning a stable result to safe retries.

## Decision

Every submission carries a client-supplied `idempotency_key`. On submit:

1. The service computes a SHA-256 `payload_fingerprint` over the semantic fields
   (everything except the key itself).
2. It looks up the key. If found:
   - **same fingerprint** → safe replay: return the original request, audit an
     `IDEMPOTENT_REPLAY` event.
   - **different fingerprint** → `IdempotencyConflictError` (client bug: key
     reused for a different payload).
3. If not found, the request and an `IDEMPOTENCY#<key>` guard are written in one
   DynamoDB `TransactWriteItems`, both conditioned on `attribute_not_exists`.

## Consequences

- The transactional write closes the check-then-act race at the storage layer:
  concurrent duplicates cannot both succeed.
- The fingerprint separates a benign retry from a genuine conflict, so retries
  are transparent and misuse is loud.
- Requires clients to send a stable key per logical request. This is documented
  in the API schema and CLI.

## Alternatives considered

- **Key-only (no fingerprint):** cannot distinguish replay from conflict; a
  reused key with a different payload would silently return the wrong result.
- **Server-generated dedup from payload hash only:** breaks when a client
  legitimately resubmits an identical payload as a *new* request.
