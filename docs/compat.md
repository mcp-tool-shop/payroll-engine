# PSP Compatibility and Versioning

## Semantic Versioning

PSP follows [Semantic Versioning 2.0.0](https://semver.org/):

```
MAJOR.MINOR.PATCH

1.0.0 → 1.0.1  (patch: bug fixes, no API changes)
1.0.0 → 1.1.0  (minor: new features, backwards compatible)
1.0.0 → 2.0.0  (major: breaking changes)
```

## What Constitutes a Breaking Change

### Breaking (Requires MAJOR version bump)

| Component | Breaking Change Examples |
|-----------|-------------------------|
| **Domain Events** | Renaming event types, removing fields from payload, changing field types |
| **Provider Protocol** | Changing method signatures, removing capabilities |
| **Facade API** | Changing method parameters, removing methods |
| **DB Schema** | Removing columns, changing column types (narrowing) |
| **Invariants** | Relaxing safety constraints |

### Non-Breaking (MINOR version bump)

| Component | Non-Breaking Change Examples |
|-----------|------------------------------|
| **Domain Events** | Adding new event types, adding optional fields to payload |
| **Provider Protocol** | Adding new optional methods, new capabilities |
| **Facade API** | Adding new methods, adding optional parameters |
| **DB Schema** | Adding columns, adding tables, adding indexes |
| **Invariants** | Tightening safety constraints |

### Patch (PATCH version bump)

- Bug fixes that don't change behavior
- Documentation updates
- Performance improvements
- Test additions

## Stability Guarantees

### Stable (Will Not Break)

These are guaranteed stable within a MAJOR version:

```
✓ Domain event types and their required fields
✓ PSP Facade public methods
✓ Provider protocol required methods
✓ DB table names and primary keys
✓ CLI command names and required arguments
✓ Invariant guarantees (ledger append-only, etc.)
```

### Unstable (May Change)

These may change in MINOR versions:

```
⚠ Internal service implementations
⚠ Private methods (prefixed with _)
⚠ Error message text
⚠ Log formats
⚠ Metric names
```

## Domain Event Versioning (Non-Negotiable)

> **This is non-negotiable.** Event versioning discipline keeps replay viable forever.

Events include a `version` field in metadata for evolution:

```python
@dataclass(frozen=True)
class EventMetadata:
    version: int = 1  # Increment for schema changes
```

### Event Versioning Rules (Enforced)

| Rule | Enforcement |
|------|-------------|
| Event names are **immutable** | CI blocks rename |
| Payload fields are **additive only** | CI blocks field removal |
| Breaking changes require **new event name** or **V2 suffix** | CI blocks breaking changes |
| All events must be **JSON-serializable** | Unit test |

### Event Evolution Rules

1. **New fields must be optional** (old consumers can ignore)
2. **Removed fields must be deprecated first** (1 MINOR version warning)
3. **Type changes require new event type** (e.g., `PaymentSettledV2`)
4. **Payload serialization is always JSON-compatible**

### CI Enforcement

The CI pipeline includes event schema validation:

```yaml
# .github/workflows/ci.yml
- name: Validate event schema evolution
  run: python scripts/check_event_compat.py
```

This script:
1. Loads the baseline event schema from `event_schema.json`
2. Compares against current event definitions
3. Fails if any breaking changes detected:
   - Renamed event types
   - Removed payload fields
   - Changed field types
   - Required fields added without default

### Stable Event Types

These event types will **never** be renamed or removed:

```
PaymentInstructionCreated
PaymentSubmitted
PaymentAccepted
PaymentSettled
PaymentReturned
PaymentFailed
LedgerEntryPosted
LedgerEntryReversed
ReservationCreated
ReservationReleased
FundingBlocked
LiabilityClassified
```

### Adding a New Event Type

1. Define the event in `src/payroll_engine/psp/events/types.py`
2. Add to `event_schema.json`
3. Write unit test for serialization
4. Update `docs/public_api.md`
5. PR requires review from maintainer

### Event Consumer Contract

Consumers MUST:
- Ignore unknown fields (forward compatibility)
- Handle missing optional fields with defaults
- Process events by type name, not version alone

Consumers SHOULD:
- Log warnings for deprecated fields
- Upgrade to new event types within deprecation window

### Deprecation Timeline

```
v1.5.0: PaymentSettled.old_field marked @deprecated
v1.6.0: PaymentSettled.old_field logged as deprecated
v2.0.0: PaymentSettled.old_field removed
```

## Provider Protocol Versioning

Providers implement a versioned protocol:

```python
class PaymentRailProvider(Protocol):
    @property
    def protocol_version(self) -> str:
        """Return protocol version (e.g., "1.0")."""
        ...

    @property
    def capabilities(self) -> RailCapabilities:
        """Return provider capabilities."""
        ...
```

### Protocol Evolution

| Protocol Version | Changes |
|-----------------|---------|
| 1.0 | Initial: submit, status, cancel, list_settlements |
| 1.1 | Added: batch_submit (optional) |
| 1.2 | Added: webhook_verify (optional) |

### Capability Discovery

New capabilities are opt-in:

```python
capabilities = provider.capabilities

if capabilities.supports_batch_submit:
    provider.batch_submit(payments)
else:
    for payment in payments:
        provider.submit(payment)
```

## Database Migration Policy

### Migration Numbering

```
migrations/
  201_ledger_tables.sql         # MAJOR feature
  202_payment_instructions.sql  # MAJOR feature
  203_funding_requests.sql      # MAJOR feature
  204_liability_attribution.sql # MINOR addition
  205_domain_events.sql         # MINOR addition
  206_constraints.sql           # MINOR tightening
```

### Migration Rules

1. **Never modify existing migrations** after release
2. **Always additive** within MINOR versions
3. **Destructive changes** (DROP, ALTER TYPE) require MAJOR version
4. **Data migrations** must be reversible for 1 version
5. **Migrations must be idempotent** (safe to run twice)

### Column Changes

```sql
-- ALLOWED in MINOR: Add nullable column
ALTER TABLE payment_instruction
    ADD COLUMN IF NOT EXISTS new_field TEXT;

-- ALLOWED in MINOR: Add column with default
ALTER TABLE payment_instruction
    ADD COLUMN IF NOT EXISTS status_reason TEXT DEFAULT 'none';

-- REQUIRES MAJOR: Remove column
ALTER TABLE payment_instruction
    DROP COLUMN old_field;

-- REQUIRES MAJOR: Change column type
ALTER TABLE payment_instruction
    ALTER COLUMN amount TYPE NUMERIC(20,4);  -- Was NUMERIC(19,4)
```

### Index Changes

Indexes can be added/removed in any version (non-breaking):

```sql
-- Safe in any version
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_new_index ON table(column);
DROP INDEX CONCURRENTLY IF EXISTS idx_old_index;
```

## API Versioning

### Facade Methods

Public methods follow signature stability:

```python
# v1.0 - Original signature
def commit_payroll_batch(self, batch: PayrollBatch) -> CommitResult:
    ...

# v1.1 - Added optional parameter (non-breaking)
def commit_payroll_batch(
    self,
    batch: PayrollBatch,
    *,
    skip_policy_check: bool = False,  # New optional param
) -> CommitResult:
    ...

# v2.0 - Changed required parameter (breaking)
def commit_payroll_batch(
    self,
    batch: PayrollBatch,
    tenant_context: TenantContext,  # New required param
) -> CommitResult:
    ...
```

### Result Types

Result dataclasses follow additive evolution:

```python
# v1.0
@dataclass
class CommitResult:
    status: CommitStatus
    batch_id: UUID

# v1.1 - Added field (non-breaking)
@dataclass
class CommitResult:
    status: CommitStatus
    batch_id: UUID
    warnings: list[str] = field(default_factory=list)  # New optional
```

## CLI Versioning

### Command Stability

```bash
# Stable commands (won't change in MAJOR version)
python -m payroll_engine.psp.cli replay-events
python -m payroll_engine.psp.cli export-events
python -m payroll_engine.psp.cli balance
python -m payroll_engine.psp.cli health

# Arguments follow same rules as API
--tenant-id    # Required, stable
--since        # Optional, stable
--format       # Optional, new choices may be added
```

### Output Format Stability

- **JSON output**: Field names stable, new fields may be added
- **Human output**: May change in any version (don't parse it)
- **Exit codes**: 0 = success, non-zero = failure (stable)

## Testing Compatibility

### Before Release Checklist

```bash
# 1. Run existing tests (must pass)
pytest tests/psp/

# 2. Run compatibility tests
pytest tests/psp/compat/

# 3. Verify migrations
python -m payroll_engine.psp.cli migrations --verify

# 4. Check event schema
python -m payroll_engine.psp.cli events --check-schema

# 5. Verify provider protocol
python -m payroll_engine.psp.cli providers --check-protocol
```

### Compatibility Test Suite

```python
# tests/psp/compat/test_event_compat.py

def test_old_event_format_still_deserializes():
    """Events from v1.0 must still parse in v1.x."""
    old_format = {
        "event_id": "...",
        "event_type": "PaymentSettled",
        "payload": {
            # v1.0 fields only
        }
    }
    event = deserialize_event(old_format)
    assert event is not None

def test_new_fields_have_defaults():
    """New fields must have defaults for old data."""
    event = PaymentSettled(...)
    assert hasattr(event, 'new_optional_field')
    assert event.new_optional_field is None  # Default
```

## Upgrade Guide Template

When releasing a new MAJOR version, provide:

```markdown
# Upgrading from v1.x to v2.0

## Breaking Changes

1. **PaymentSettled event**: `old_field` removed
   - Migration: Use `new_field` instead
   - Deadline: Was deprecated in v1.5

2. **commit_payroll_batch**: Now requires `tenant_context`
   - Migration: Pass TenantContext object
   ```python
   # Before (v1.x)
   result = psp.commit_payroll_batch(batch)

   # After (v2.0)
   result = psp.commit_payroll_batch(batch, tenant_context)
   ```

## Migration Steps

1. Run database migrations
2. Update event consumers
3. Update API calls
4. Run compatibility tests

## Rollback Plan

If issues occur:
1. Stop new deployments
2. Run rollback migration: `206_rollback.sql`
3. Deploy previous version
```

## Support Policy

| Version | Support Level |
|---------|--------------|
| Current MAJOR | Full support |
| Previous MAJOR | Security fixes for 12 months |
| Older | No support |

## Questions?

For compatibility questions:
- Check existing issues
- Open a discussion
- Contact maintainers
