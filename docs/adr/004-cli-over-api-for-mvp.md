# ADR 004: CLI as the MVP interface, API as the integration boundary

- Status: Accepted
- Date: 2026-07-17

## Context

The control plane needs a usable local interface to demonstrate the full
lifecycle without deploying AWS infrastructure. A production deployment would
expose an authenticated HTTP API (API Gateway + OIDC), but standing that up
locally adds a transport, an auth mock, and a server lifecycle that dilute the
demonstration of the actual control-plane behaviour.

## Decision

Ship a dependency-free `argparse` CLI (`control-plane`) as the MVP interface. It
calls the same `ControlPlaneService` a future API handler would call, so the
domain, policy, approval, and audit logic is exercised identically. The CLI is
backed by a file-persisting in-memory repository.

The HTTP API is deliberately scoped out of the MVP and documented as the
integration boundary (see [production-boundaries.md](../production-boundaries.md)
and the threat model's identity-extraction requirement).

## Consequences

- The full lifecycle is demonstrable and testable end-to-end with `pip install`
  and no cloud account.
- The service seam is validated by the CLI, so adding a thin FastAPI/Lambda
  handler later is low-risk.
- Identity/authorization (OIDC → verified `owner`/`approver`) is NOT enforced by
  the CLI; it is an API-layer responsibility and is documented as such.

## Alternatives considered

- **FastAPI now:** more "real," but adds transport/auth scaffolding that the MVP
  does not need to prove the control-plane behaviour, and risks presenting an
  unauthenticated API as production-shaped.
