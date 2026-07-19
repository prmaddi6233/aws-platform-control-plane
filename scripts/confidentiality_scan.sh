#!/usr/bin/env bash
# Fail if likely-confidential data appears in tracked source.
# Synthetic placeholders (documented in the README) are allowed.
set -euo pipefail

cd "$(dirname "$0")/.."

# Directories/files that never contain source we publish.
EXCLUDES=(--exclude-dir=.git --exclude-dir=.venv --exclude-dir=.terraform
  --exclude-dir=.mypy_cache --exclude-dir=.ruff_cache --exclude-dir=.pytest_cache
  --exclude-dir='*.egg-info' --exclude='*.pyc')

# Synthetic account IDs and example IDs that are intentionally public.
# Synthetic/example IDs that are intentionally public (AWS-docs style + repeated digits).
ALLOWED_IDS='111111111111|222222222222|333333333333|000000000000|123456789012|111122223333|444455556666'

fail=0
report() { echo "CONFIDENTIALITY: $1"; fail=1; }

# 1. AWS access keys / secrets / private keys / tokens.
if grep -RInE 'AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|aws_secret_access_key\s*=' . "${EXCLUDES[@]}" \
    | grep -v 'scripts/confidentiality_scan.sh'; then
  report "possible credential/secret found"
fi

# 2. Real-looking 12-digit AWS account IDs (excluding synthetic placeholders).
if grep -RInE '\b[0-9]{12}\b' . "${EXCLUDES[@]}" --include='*.py' --include='*.tf' \
    --include='*.json' --include='*.md' --include='*.yml' --include='*.yaml' \
    | grep -vE "$ALLOWED_IDS" | grep -v 'scripts/confidentiality_scan.sh'; then
  report "possible real AWS account ID found"
fi

# 3. Local absolute paths.
if grep -RInE '/Users/[a-zA-Z]+|/home/[a-zA-Z]+' . "${EXCLUDES[@]}" \
    --include='*.py' --include='*.tf' --include='*.json' --include='*.md' --include='*.yml' \
    | grep -v 'scripts/confidentiality_scan.sh'; then
  report "local absolute path found"
fi

if [ "$fail" -eq 0 ]; then
  echo "confidentiality scan: clean"
fi
exit "$fail"
