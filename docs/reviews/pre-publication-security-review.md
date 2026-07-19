# Pre-Publication Security & Hygiene Review

**Date:** 2026-07-18
**Repository:** `aws-platform-control-plane` (pre-`git init`)
**Scope:** source, IaC, workflows, CI, and repository hygiene prior to first commit.

## Method

- `bash scripts/confidentiality_scan.sh` (credentials, real account IDs, local paths)
- Manual review of `.github/workflows/`, `.gitignore`, Terraform, and docs
- Grep sweeps for secrets, tokens, employer/customer names, internal URLs, tickets

## Findings

### Critical
None.

### High
None.

### Medium
None outstanding. (Resolved this cycle: CI third-party actions that could not be
SHA-verified offline were **not** pinned to fabricated SHAs — see Low-1.)

### Low
- **Low-1 — Unverified action SHA pins.** `setup-tflint`, `checkov-action`, and
  `pre-commit/action` are tag-pinned with a `TODO(security)` comment because their
  exact commit SHAs could not be verified in this offline environment. `trivy-action`
  retains its existing pin. **Action:** resolve and pin real SHAs before enabling the
  security/pre-commit jobs on a public remote. First-party `actions/*` and
  `hashicorp/setup-terraform` are pinned to verified SHAs.
- **Low-2 — Personal security-contact email.** `SECURITY.md` lists a personal Gmail
  as the vulnerability-report contact. Intentional for a solo portfolio; consider a
  dedicated alias.

## Confidentiality scan — result

```
confidentiality scan: clean
```

No credentials, private keys, real AWS account IDs, local absolute paths, employer
or customer names, internal URLs, or ticket numbers found. All account IDs are
synthetic placeholders (`111111111111`, AWS-docs `111122223333`, etc.).

## GitHub Actions review

| Concern | Status |
|---------|--------|
| Excessive permissions | **OK** — top-level `permissions: contents: read`; no job escalates. |
| Pull-request secret exposure | **OK** — no `pull_request_target`; no `secrets` referenced. |
| Untrusted script execution | **OK** — only trusted first-party/well-known actions + repo scripts. |
| Floating action tags | **Partial** — first-party pinned to SHA; three third-party tag-pinned (Low-1). |
| Dependency confusion | **OK** — editable install of this package; dev deps are versioned. |
| Artifact leakage | **OK** — no artifacts uploaded; no caches expose secrets. |

## Repository hygiene (`.gitignore`)

Verified ignored: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`,
`.ruff_cache/`, `.terraform/`, `*.tfstate`, `*.tfstate.*`, `.env`, `.env.*`,
`dist/`, `build/`, `*.egg-info/`, `.DS_Store`, `.control-plane-state.json`,
`control-plane-store.json`.

## Gate for publication

No Critical or High findings. Low-1 must be resolved (real SHAs) **before** the
repository is pushed to a public remote; it does not block local commits.
