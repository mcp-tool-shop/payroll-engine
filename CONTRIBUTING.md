# Contributing to PSP

Thank you for considering contributing to PSP. This document explains how to contribute effectively.

## Code of Conduct

Be professional. Be respectful. Focus on the code.

## Getting Started

```bash
# Clone the repo
git clone https://github.com/payroll-engine/payroll-engine.git
cd payroll-engine

# Set up environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Start PostgreSQL
make up

# Apply migrations
make migrate

# Run tests
make test
```

## Definition of Done

Every PR must satisfy ALL of the following before merge:

### 1. Tests Pass

```bash
# All tests must pass
make test
make test-psp

# Type checking must pass
pyright src/
```

### 2. Invariant Impact Statement

If your change affects money movement, ledger entries, or funding gates:

- [ ] Add an "Invariant Impact" section to your PR description
- [ ] Explain which invariants are affected
- [ ] Prove (via tests) that invariants still hold

Example:
```markdown
## Invariant Impact

This PR adds a new entry type for adjustments.

**Affected invariants:**
- Ledger balance: Still holds - adjustments are balanced entries
- Amount positive: Still holds - CHECK constraint unchanged

**New test:** `test_adjustment_entries_remain_balanced`
```

### 3. Migration + Schema Check

If your change touches the database:

- [ ] Add migration file with correct numbering
- [ ] Migration is idempotent (safe to run twice)
- [ ] `psp schema-check` passes after migration
- [ ] Update `docs/upgrading.md` if needed

### 4. Event Compatibility

If your change touches domain events:

- [ ] Run `python scripts/check_event_compat.py`
- [ ] No required fields removed from existing events
- [ ] No event types renamed or removed
- [ ] New events added to `event_schema.json`

### 5. Public API

If your change touches the public API:

- [ ] Update `docs/public_api.md`
- [ ] Consider backwards compatibility
- [ ] Write an RFC if it's a breaking change

### 6. Documentation

- [ ] Update relevant docs if behavior changes
- [ ] Add docstrings to new public functions
- [ ] Update runbooks if operational behavior changes

### 7. Clean History

- [ ] Commits are atomic and meaningful
- [ ] No WIP commits in final PR
- [ ] Commit messages explain "why" not "what"

## What to Contribute

### Good First Issues

Look for issues labeled `good first issue`. These are:
- Well-defined scope
- Limited codebase knowledge needed
- Mentoring available

### Areas Needing Help

- **Documentation**: Recipes, examples, clarifications
- **Testing**: Edge cases, property tests, integration tests
- **Providers**: Implementations for specific banks/processors

### Areas Requiring Experience

These require deep understanding of PSP:
- Ledger service changes
- Funding gate logic
- Event schema modifications

Please discuss in an issue before starting work in these areas.

## Pull Request Process

1. **Fork** the repo and create a feature branch
2. **Make** your changes following the Definition of Done
3. **Test** locally with `make test && make test-psp`
4. **Push** to your fork and open a PR
5. **Respond** to review feedback
6. **Squash** if requested
7. **Celebrate** when merged

### PR Title Format

```
type: short description

Examples:
fix: prevent negative amounts in adjustments
feat: add FedNow provider implementation
docs: clarify idempotency key requirements
chore: update CI to Python 3.12
```

Types: `fix`, `feat`, `docs`, `chore`, `refactor`, `test`

### PR Description Template

```markdown
## Summary
What does this PR do?

## Motivation
Why is this change needed?

## Changes
- Change 1
- Change 2

## Testing
How was this tested?

## Invariant Impact
(If applicable - see Definition of Done)

## Checklist
- [ ] Tests pass
- [ ] Docs updated
- [ ] Schema check passes
- [ ] Event compat check passes
```

## Review Process

All PRs require:
- 1 approval from a maintainer
- All CI checks passing
- No unresolved conversations

See [CODEOWNERS](CODEOWNERS) for who reviews what.

## Release Process

Maintainers handle releases. See [RELEASING.md](RELEASING.md) for the full checklist.

Contributors don't need to worry about releases.

---

## Long-Term Maintenance Posture

### Supported Change Types

**Allowed without major version:**
- New event types
- Additive event fields
- New provider implementations
- Stricter constraints (never looser)
- New CLI commands (no breaking flags)

**Requires major version:**
- Changing public facade signatures
- Changing provider protocol semantics
- Changing event meanings (not just shape)
- Relaxing ledger or funding invariants

### What Happens If a Bug Touches Money

**Non-negotiable rules:**
1. **Never rewrite ledger history**
2. **Never delete events**
3. Corrections happen via:
   - Reversal entries
   - Compensating events
   - Explicit liability records

**Every such bug must add:**
- A regression test
- A red-team scenario (if applicable)
- A changelog entry under "Fixed"

### Invariant Preservation

When reviewing PRs that touch money movement:

| Question | Required Answer |
|----------|-----------------|
| Does it preserve positive amounts? | Yes, via CHECK constraint |
| Does it preserve balanced entries? | Yes, debits = credits |
| Does it preserve append-only? | Yes, no UPDATE/DELETE on ledger |
| Does it preserve idempotency? | Yes, same key = same result |

If any answer is "No" or "Maybe", the PR needs more work.

---

## Questions?

- Open a [Discussion](https://github.com/payroll-engine/payroll-engine/discussions)
- Check existing [Issues](https://github.com/payroll-engine/payroll-engine/issues)
- Read the [Documentation](docs/)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
