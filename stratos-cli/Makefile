PYTHON ?= python

.PHONY: help install lint format typecheck test check audit sbom build docs clean

help:            ## Show targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'

install:         ## Install with all extras and dev tools
	$(PYTHON) -m pip install -e ".[openai,pdf]" pytest ruff mypy types-PyYAML

lint:            ## Style and static checks
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:          ## Auto-format
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

typecheck:       ## Strict type check
	$(PYTHON) -m mypy

test:            ## Run the test suite
	$(PYTHON) -m pytest -q

check: lint typecheck test   ## Everything CI runs

audit:           ## Dependency vulnerability scan
	$(PYTHON) -m pip_audit --strict --skip-editable

sbom:            ## Software bill of materials (CycloneDX)
	$(PYTHON) -m cyclonedx_py environment --output-format JSON --output-file sbom.cdx.json

build:           ## Build wheel and sdist
	$(PYTHON) -m build

docs:            ## Regenerate docs/CLI_REFERENCE.md and the progress log
	$(PYTHON) scripts/gen_cli_reference.py
	node scripts/build_progress_log.js

clean:
	rm -rf dist build .mypy_cache .ruff_cache .pytest_cache
