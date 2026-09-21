PYTHON ?= python

.PHONY: help setup install lint format typecheck test integration-test e2e coverage check security audit sbom build verify-package docs release clean mock-services

help:            ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

setup:           ## One-shot developer setup (venv and dependencies)
	$(PYTHON) scripts/bootstrap.py

install:         ## Install with all extras and dev tools
	$(PYTHON) -m pip install -e ".[openai,pdf]" pytest pytest-cov ruff mypy types-PyYAML build pip-audit cyclonedx-bom

lint:            ## Style and static checks
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:          ## Auto-format
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

typecheck:       ## Strict type check
	$(PYTHON) -m mypy

test:            ## Unit tests
	$(PYTHON) -m pytest -q tests/unit

integration-test: ## Integration tests (several layers, external services faked)
	$(PYTHON) -m pytest -q -m integration tests/integration

e2e:             ## End-to-end tests (real entry point in a subprocess)
	$(PYTHON) -m pytest -q -m e2e tests/e2e

coverage:        ## Full suite with the coverage gate (fails below 90%)
	$(PYTHON) -m pytest -q --cov --cov-report=term-missing:skip-covered

check: lint typecheck coverage   ## Everything CI runs on every change

security:        ## Dependency vulnerability scan
	$(PYTHON) -m pip_audit --strict --skip-editable

audit: security

sbom:            ## Software bill of materials (CycloneDX)
	$(PYTHON) -m cyclonedx_py environment --output-format JSON --output-file sbom.cdx.json

build:           ## Build wheel and sdist
	$(PYTHON) -m build

verify-package:  ## Build, install in a clean venv and smoke-test the wheel
	$(PYTHON) scripts/verify_package.py

docs:            ## Regenerate docs/CLI_REFERENCE.md and the progress log
	$(PYTHON) scripts/gen_cli_reference.py
	node scripts/build_progress_log.js

release: check build verify-package   ## Everything needed before tagging vX.Y.Z
	@echo "Now update CHANGELOG.md and tag the release."

mock-services:   ## Offline stand-ins for the identity provider and Platform API
	$(PYTHON) scripts/mock_services.py

clean:
	rm -rf dist build .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
