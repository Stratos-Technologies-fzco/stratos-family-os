## What and why

## Definition of Done
- [ ] `make check` passes (ruff, format, strict mypy, tests, coverage >= 90%)
- [ ] New behaviour has unit tests (integration/e2e where several layers or the entry point are involved)
- [ ] Destructive or multi-step operations use `OperationGuard` / `Transaction` and are audited
- [ ] No secrets in code, logs, fixtures or docs
- [ ] `docs/CLI_REFERENCE.md` regenerated (`make docs`) and static help in `cli/app.py` updated if commands changed
- [ ] `CHANGELOG.md`, ADR and progress log updated where relevant
