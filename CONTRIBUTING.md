# Contributing

Thank you for your interest in contributing to this project.

## How to Contribute

1. **Fork** the repository.
2. **Create a branch** from `main` for your change.
3. **Make your changes** following the standards below.
4. **Run validation** before submitting:

   ```bash
   make lint
   make test
   ```

5. **Open a pull request** with a clear description of what you changed and why.

## Standards

### Python

- Python 3.12+ (CI runs the test suite on 3.12)
- Type hints on all public functions and methods
- Pydantic models for data validation
- `pytest` for testing
- `ruff` for linting and formatting

### Terraform

- `terraform fmt` compliance
- `terraform validate` passes
- Variables have descriptions and validation blocks where appropriate

### Documentation

- Update relevant docs when changing behaviour
- Architecture Decision Records for significant design changes
- All examples use synthetic data — no real AWS account IDs or identifiers

### Commits

- Use clear, descriptive commit messages
- One logical change per commit
- Reference related issues where applicable

## Code of Conduct

Be respectful, constructive, and professional in all interactions.
