# Upgrading PSP

This document provides the muscle-memory playbook for upgrading PSP versions.

## Design Assumptions

- **Forward-only migrations** - No rollback support
- **Append-only ledgers and event stores** - Never UPDATE/DELETE
- **Additive-only event evolution** - Unless explicitly versioned (V2)

## Philosophy: Roll-Forward Only

PSP migrations are **forward-only**. We do not provide rollback migrations because:

1. Financial data cannot be "un-happened"
2. Rollbacks risk data loss or corruption
3. Testing rollback paths doubles maintenance burden

If an upgrade fails, the recovery path is:
1. Stop the upgrade
2. Fix the issue
3. Continue forward

## Pre-Upgrade Checklist

### 1) Snapshot what matters

Export domain events (for offline analysis / replay fallback):
```bash
psp export-events --database-url "$DATABASE_URL" --output events_backup.jsonl
```

### 2) Confirm version policy

Read:
- [docs/compat.md](compat.md) - Compatibility guarantees
- [docs/public_api.md](public_api.md) - Public API contract
- [CHANGELOG.md](../CHANGELOG.md) - What changed

If the upgrade includes new events, confirm additive-only fields or versioned events (V2).

### 3) Run pre-upgrade checks

Before any upgrade:

```bash
# 1. Check current version
pip show payroll-engine

# 2. Read the CHANGELOG for your target version
# Look for: Breaking Changes, Migration Required, Deprecations

# 3. Run health check on current system
psp health

# 4. Backup database (non-negotiable)
pg_dump -Fc payroll_db > backup_$(date +%Y%m%d).dump

# 5. Check current schema state
psp schema-check --database-url $DATABASE_URL
```

## Upgrade Steps

### Step 1: Install New Version

```bash
# In a test environment first!
pip install payroll-engine==X.Y.Z

# Or from source
pip install -e ".[dev]"
```

### Step 2: Check Migration Requirements

```bash
# See what migrations are pending
python scripts/migrate.py --dry-run

# Expected output shows migrations to apply
```

### Step 3: Apply Migrations

```bash
# Apply in a transaction (if supported)
python scripts/migrate.py --database-url $DATABASE_URL

# Migrations are idempotent - safe to run twice
```

### Step 4: Verify Schema

```bash
# This MUST pass
psp schema-check --database-url $DATABASE_URL

# Expected output:
# Schema verification: PASSED
# All required tables, constraints, and triggers are present.
```

### Step 5: Run Health Check

```bash
psp health --component all

# All components should be healthy
```

### Step 6: Verify Event Replay

```bash
# Replay recent events to verify compatibility
psp replay-events --tenant-id $TENANT --since "$(date -d '1 hour ago' -Iseconds)" --dry-run

# Should show events without errors
```

### Step 7: Run Integration Tests

```bash
# Your application's integration tests
pytest tests/integration/ -v

# Or PSP's own tests
pytest tests/psp/ -v
```

## Upgrade Checklist (Copy/Paste)

```
[ ] Read CHANGELOG.md for the target version
[ ] Export events: psp export-events --output events_backup.jsonl
[ ] Backup database: pg_dump -Fc payroll_db > backup.dump
[ ] Run psp schema-check (pre)
[ ] Install new version
[ ] Apply migrations (forward-only)
[ ] Run psp schema-check (post)
[ ] Run psp replay-events spot-check
[ ] Run psp health
[ ] Run integration tests
[ ] If any ops behavior changed, review runbooks
```

---

## Version-Specific Upgrade Guides

### Upgrading to 0.2.x from 0.1.x

*(This section will be populated when 0.2.0 is released)*

```
No breaking changes expected.
Migrations: 207_xxx.sql (additive only)
Event changes: None
API changes: None
```

### Upgrading to 1.0.x from 0.x

*(This section will be populated when 1.0.0 is released)*

```
Breaking changes: TBD
Migration steps: TBD
Deprecation removals: TBD
```

## Migration Application Order

Migrations are numbered and must be applied in order:

```
201_ledger_tables.sql
202_payment_instructions.sql
203_funding_requests.sql
204_liability_attribution.sql
205_domain_events.sql
206_impossible_state_constraints.sql
...
```

The migration runner tracks applied migrations in `psp_schema_migrations` table:

```sql
SELECT * FROM psp_schema_migrations ORDER BY applied_at;
```

## Event Replay Expectations

After upgrade, event replay should:

1. **Parse all historical events** - Old event formats must still deserialize
2. **Produce same results** - Replaying events should produce identical state
3. **Handle new fields gracefully** - New optional fields have defaults

Verify with:

```bash
# Export events before upgrade
psp export-events --tenant-id $TENANT --output before.jsonl

# After upgrade, export again
psp export-events --tenant-id $TENANT --output after.jsonl

# Compare (should be identical except for new optional fields)
diff <(jq -S . before.jsonl) <(jq -S . after.jsonl)
```

## What To Do If Migration Fails

### Scenario: Migration Script Errors

```bash
# 1. Check error message
# 2. DO NOT manually modify the database
# 3. Fix the issue (usually config or permissions)
# 4. Re-run migration (they're idempotent)

python scripts/migrate.py --database-url $DATABASE_URL
```

### Scenario: Schema Check Fails After Migration

```bash
# 1. Identify what's missing
psp schema-check --database-url $DATABASE_URL

# 2. Check if migration actually ran
psql -c "SELECT * FROM psp_schema_migrations ORDER BY applied_at DESC LIMIT 5;"

# 3. If migration recorded but schema wrong, contact support
# 4. If migration not recorded, re-run
```

### Scenario: Event Compatibility Error

If an event changed in a breaking way, you must:
- Introduce a new event name/version (`...V2`), OR
- Restore additive-only compatibility

**Never "edit old events."** Append compensating facts.

### Scenario: Ledger Inconsistency Suspected

Use runbooks and evidence trails:
- [docs/invariants.md](invariants.md)
- [docs/runbooks/](runbooks/)

**Correct via reversal entries and liability events (never UPDATE/DELETE).**

### Scenario: Application Errors After Upgrade

```bash
# 1. Check logs for specific errors
# 2. Verify config compatibility (new required fields?)
# 3. Check event compatibility

python scripts/check_event_compat.py

# 4. If events incompatible, you may need to:
#    - Update your event consumers
#    - Or rollback package version (not DB)
pip install payroll-engine==<previous_version>
```

### Scenario: Performance Degradation

```bash
# 1. Check for missing indexes
psp schema-check --database-url $DATABASE_URL

# 2. Analyze slow queries
psql -c "SELECT query, calls, mean_time FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10;"

# 3. Run ANALYZE on affected tables
psql -c "ANALYZE psp_ledger_entry; ANALYZE payment_instruction;"
```

## Verifying Success

After upgrade, run this verification sequence:

```bash
#!/bin/bash
set -e

echo "=== PSP Upgrade Verification ==="

echo "1. Schema check..."
psp schema-check --database-url $DATABASE_URL

echo "2. Health check..."
psp health

echo "3. Event compatibility..."
python scripts/check_event_compat.py

echo "4. Sample balance query..."
psp balance --tenant-id $TENANT --account-id $ACCOUNT

echo "5. Event replay test..."
psp replay-events --tenant-id $TENANT --limit 10 --dry-run

echo "=== All checks passed ==="
```

## Deprecation Handling

When a feature is deprecated:

1. **v1.5.0**: Feature marked `@deprecated` in code, logged at WARN level
2. **v1.6.0**: Feature logs at ERROR level, still works
3. **v2.0.0**: Feature removed

To find deprecated usage in your code:

```bash
# Search for deprecated imports
grep -r "from payroll_engine.psp" your_code/ | grep -v "__pycache__"

# Run with deprecation warnings visible
python -W default::DeprecationWarning your_script.py
```

## Getting Help

If upgrade issues persist:

1. Check [GitHub Issues](https://github.com/payroll-engine/payroll-engine/issues)
2. Search for your error message
3. Open a new issue with:
   - Current version
   - Target version
   - Error message
   - Output of `psp schema-check`
   - Output of `psp health`
