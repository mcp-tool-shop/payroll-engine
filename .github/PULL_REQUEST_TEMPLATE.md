## Summary

<!-- Brief description of what this PR does -->

## What changed

<!-- List the specific changes made -->

-

## Checklist

### Code Quality

- [ ] Tests added/updated for all changes
- [ ] All tests pass locally (`make test`)
- [ ] Type checking passes (`pyright`)
- [ ] Linting passes (`ruff check`)

### Impact Assessment

- [ ] **Invariants**: Does this affect any [system invariants](docs/psp_invariants.md)?
  - If yes, explain:
- [ ] **Public API**: Does this change the [public API](docs/public_api.md)?
  - If yes, is it additive (minor) or breaking (major)?
- [ ] **Events**: Does this add/modify [domain events](docs/public_api.md#4-domain-events)?
  - If yes, is it backwards-compatible?
- [ ] **Migrations**: Does this require database migrations?
  - If yes, are they forward-compatible?

### Documentation

- [ ] Updated relevant docs if behavior changed
- [ ] Updated `docs/public_api.md` if public API changed
- [ ] Updated runbooks if operational procedures changed
- [ ] Added/updated code comments where logic isn't self-evident

### Money Movement (if applicable)

- [ ] This PR does NOT bypass funding gates
- [ ] This PR does NOT allow negative balances (unless explicitly permitted)
- [ ] This PR does NOT enable AI to move money or write ledger entries
- [ ] Idempotency keys are used for all new operations

## Testing

<!-- How did you test this? -->

- [ ] Unit tests
- [ ] Integration tests with database
- [ ] Manual testing (describe):

## Related Issues

<!-- Link any related issues -->

Fixes #
Related to #

## Screenshots (if applicable)

<!-- Add screenshots for UI/output changes -->
