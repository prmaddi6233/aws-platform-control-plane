# Threat Model: Platform Control Plane

## Scope
The AWS Platform Control Plane APIs, Step Functions workflows, and persistence layer.

## Actors
- **Developer:** Authenticated corporate identity requesting an account.
- **Approver:** Platform engineer or manager approving requests.
- **Workflow Role:** The IAM role assumed by Step Functions to create accounts.
- **Attacker:** Internal user attempting to escalate privileges or bypass controls.

## Threats & Mitigations

### 1. Spoofing
**Threat:** A developer submits a request asserting they are a different owner.
**Mitigation:** The API gateway must extract the identity from the verified OIDC/SAML token and inject it into the payload, overriding any client-provided `requester` field.

### 2. Tampering
**Threat:** An attacker modifies the DynamoDB state to skip the `APPROVED` phase.
**Mitigation:** DynamoDB access is restricted via IAM. The API roles only have `PutItem` (with `attribute_not_exists` conditions) and `UpdateItem` limited to their own requests. No user-facing role has raw write access to the table.

### 3. Repudiation
**Threat:** A user claims they didn't request an account that incurred thousands in charges.
**Mitigation:** Every state transition emits an immutable domain event to EventBridge, which is archived in a tamper-evident S3 log bucket (WORM).

### 4. Information Disclosure
**Threat:** The API returns internal AWS account metadata or failed workflow logs to unprivileged users.
**Mitigation:** The `ErrorResponse` schema sanitizes exceptions. Step Functions logs are not exposed via the API. `AccountResponse` only returns explicitly defined, safe fields.

### 5. Denial of Service
**Threat:** A script submits thousands of account requests, exhausting AWS Organizations limits.
**Mitigation:**
- The API is rate-limited.
- Idempotency keys prevent duplicate submission retries from triggering new workflows.
- A hard concurrency limit is placed on the Step Functions execution role to prevent flooding the Organizations API.

### 6. Elevation of Privilege
**Threat:** A user requests an account with a highly privileged IAM Identity Center permission set assigned to themselves.
**Mitigation:** The API schema strictly defines what can be requested. Permission set assignment is hardcoded in the Step Functions workflow based on the `environment` and `data_classification` fields. Users cannot arbitrarily request "AdministratorAccess".
