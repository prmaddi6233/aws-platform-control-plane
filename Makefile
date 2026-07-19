.PHONY: help lint format typecheck test test-all tf-fmt tf-validate tf-lint \
        security-scan confidentiality validate-fast validate-python \
        validate-terraform validate-security validate clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# Fail clearly when a required external tool is missing.
define require_tool
	@command -v $(1) >/dev/null 2>&1 || { \
		echo "ERROR: '$(1)' is required but not installed."; \
		echo "Install: $(2)"; exit 127; }
endef

# ── Python ──────────────────────────────────────────────────────────────
lint: ## Run ruff lint + format check
	ruff check src/ tests/
	ruff format --check src/ tests/

format: ## Auto-format Python code
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck: ## Run mypy --strict
	mypy src/

test: ## Run unit tests
	pytest -q

test-all: ## Run all tests with coverage
	pytest tests/ -v --cov=platform_control_plane --cov-report=term-missing

# ── Terraform ───────────────────────────────────────────────────────────
tf-fmt: ## Check Terraform formatting
	$(call require_tool,terraform,https://developer.hashicorp.com/terraform/install)
	terraform -chdir=infrastructure/terraform fmt -check -recursive

tf-validate: ## Validate Terraform (no backend)
	$(call require_tool,terraform,https://developer.hashicorp.com/terraform/install)
	terraform -chdir=infrastructure/terraform init -backend=false >/dev/null
	terraform -chdir=infrastructure/terraform validate

tf-lint: ## Run TFLint
	$(call require_tool,tflint,https://github.com/terraform-linters/tflint#installation)
	cd infrastructure/terraform && tflint --recursive

# ── Security ────────────────────────────────────────────────────────────
security-scan: ## Run Trivy + Checkov config scans
	$(call require_tool,trivy,https://aquasecurity.github.io/trivy/latest/getting-started/installation/)
	trivy config --exit-code 1 infrastructure/terraform
	$(call require_tool,checkov,pipx install checkov)
	checkov -d infrastructure/terraform --quiet --compact

confidentiality: ## Scan tracked source for confidential data
	bash scripts/confidentiality_scan.sh

# ── Graduated validation ────────────────────────────────────────────────
validate-fast: lint typecheck test confidentiality ## Checks from project deps only (no external tools)

validate-python: lint typecheck test ## Python quality gate

validate-terraform: tf-fmt tf-validate tf-lint ## Terraform gate (needs terraform + tflint)

validate-security: security-scan confidentiality ## Security gate (needs trivy + checkov)

validate: validate-python validate-terraform validate-security ## Full gate (fails clearly on missing tools)

clean: ## Remove build/test artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist/ build/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
