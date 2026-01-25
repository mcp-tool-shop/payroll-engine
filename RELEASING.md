# Releasing the PSP Library

This checklist is designed to prevent silent financial regressions and preserve compatibility promises.

## Release Types

| Type | When to Use |
|------|-------------|
| **Patch** | Bug fixes, internal refactors, doc improvements, additive metrics |
| **Minor** | Additive API/event fields, new event types, additive migrations |
| **Major** | Breaking public API, breaking events, migration policy change |

## Pre-Release Checklist

### A) Contract & Compatibility

- [ ] `docs/public_api.md` is accurate (no accidental new public surface)
- [ ] `docs/compat.md` is accurate (versioning rules unchanged or explicitly updated)
- [ ] Event compatibility check passes:
  ```bash
  python scripts/check_event_compat.py
  ```
- [ ] If any event payload changed:
  - [ ] Changes are additive-only, OR
  - [ ] Introduced `...V2` event (breaking change avoided)
- [ ] If provider protocol changed:
  - [ ] Versioned appropriately per `docs/compat.md`

### B) Database & Migrations

- [ ] New migrations are forward-only and numbered correctly
- [ ] Migrations include constraints/triggers for "impossible states" where applicable
- [ ] `psp schema-check` passes on a clean DB after applying *all* migrations:
  ```bash
  psp schema-check --database-url "$DATABASE_URL"
  ```
- [ ] `psp schema-check` passes on an upgraded DB (previous version → new version)
- [ ] Smoke tests include at least one "negative test" that asserts constraints prevent corruption

### C) Money Safety & Idempotency

- [ ] Any money-affecting change includes an invariant impact statement in PR description
- [ ] Idempotency semantics unchanged OR explicitly documented in `docs/idempotency.md`
- [ ] All idempotent writes return canonical records (`created|already_exists`) and never assume insert succeeded
- [ ] Reversal semantics unchanged or strengthened (never weakened)

### D) Tests & CI

- [ ] Unit tests pass:
  ```bash
  pytest tests/ --ignore=tests/psp/test_red_team_scenarios.py
  ```
- [ ] Red-team tests pass:
  ```bash
  pytest tests/psp/test_red_team_scenarios.py -v
  ```
- [ ] DB-backed CI job passes (migrate + constraint checks)
- [ ] Lint/type checks pass:
  ```bash
  ruff check src/ tests/
  pyright src/
  ```
- [ ] Coverage meets minimum threshold (if enforced)

### E) Docs & Operator Experience

- [ ] Adoption Kit updated: `docs/adoption_kit.md`
- [ ] Runbooks updated if operational behavior changed:
  - [ ] returns
  - [ ] settlement mismatch
  - [ ] funding gate blocks
  - [ ] replay domain events
- [ ] README is accurate and still "serious user" oriented
- [ ] `docs/upgrading.md` updated if upgrade steps changed

### F) Packaging

- [ ] `pyproject.toml` version bumped
- [ ] Optional dependency groups validated:
  ```bash
  pip install -e ".[postgres]"
  pip install -e ".[all]"
  ```
- [ ] CLI entry works:
  ```bash
  psp --help
  ```

---

## Release Process

### 1) Choose version

- Patch/minor/major according to `docs/compat.md`

### 2) Update changelog

Add release notes to `CHANGELOG.md`:
- User-visible changes
- Migrations added
- Event changes (new types/fields)
- Operational notes

### 3) Run the full local verification

```bash
make up
make migrate
make demo
pytest
psp schema-check --database-url "$DATABASE_URL"
```

### 4) Tag and publish

Create git tag pointing to the release commit:
```bash
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

Build and publish to PyPI (if applicable):
```bash
python -m build
twine upload dist/*
```

### 5) Post-release sanity

- [ ] Install from published artifact in a clean virtualenv
- [ ] Run `psp --help` and `examples/psp_minimal` against a fresh DB
- [ ] Confirm `scripts/check_event_compat.py` passes against the tag

---

## Hotfix Policy

If a financial correctness bug is discovered:

1. **Roll forward** with a patch release
2. **Never rewrite** ledger history
3. **Use reversals** and additional facts (events/settlements/liability) to correct
4. **Add a regression test** reproducing the incident scenario

---

## Release Notes Template

```markdown
## [vX.Y.Z] — YYYY-MM-DD

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Database / Migration Notes
- Migrations added: `NNN_description.sql`
- Constraints added: ...
- (or "No migration changes")

### Event Compatibility
- New events: ...
- New fields: ... (additive-only)
- (or "No event changes")

### Operational Notes
- Runbook updates: ...
- CLI changes: ...
- (or "No operational changes")
```

---

## Version History

| Version | Date | Type | Notes |
|---------|------|------|-------|
| 0.1.0 | 2025-01-25 | Initial | First public release |
