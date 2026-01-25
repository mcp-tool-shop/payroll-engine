# Public API Contract

> **This document is a contract, not documentation.**
> If it's not listed here, it's not stable. If you import it anyway, that's on you.

## Stability Guarantees

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes to anything in this document
- **MINOR**: Additive changes (new methods, new events, new optional params)
- **PATCH**: Bug fixes, performance improvements, internal refactors

## Public Surface

### 1. PSP Facade

**Import path**: `from payroll_engine.psp import PSP`

```python
class PSP:
    """The single entry point for all PSP operations."""

    def __init__(
        self,
        session: Session,
        config: PSPConfig,
    ) -> None: ...

    # === Stable Methods ===

    def commit_payroll_batch(
        self,
        batch: PayrollBatch,
    ) -> CommitResult: ...

    def execute_payments(
        self,
        tenant_id: UUID,
        legal_entity_id: UUID,
        batch_id: UUID,
        scheduled_date: date,
        provider_name: str | None = None,
    ) -> ExecuteResult: ...

    def ingest_settlement_feed(
        self,
        tenant_id: UUID,
        bank_account_id: UUID,
        provider_name: str,
        records: list[SettlementRecord],
    ) -> IngestResult: ...

    def handle_provider_callback(
        self,
        tenant_id: UUID,
        provider_name: str,
        callback_type: str,
        payload: dict,
    ) -> CallbackResult: ...

    def get_balance(
        self,
        tenant_id: UUID,
        account_id: UUID,
    ) -> BalanceResult: ...

    def replay_events(
        self,
        tenant_id: UUID,
        after: datetime | None = None,
        before: datetime | None = None,
        event_types: list[str] | None = None,
        limit: int = 1000,
    ) -> Iterator[DomainEvent]: ...
```

**Stability**: All methods listed above are stable. New methods may be added (minor version bump). Method signatures will not change without major version bump.

### 2. Configuration Objects

**Import path**: `from payroll_engine.psp import PSPConfig, LedgerConfig, FundingGateConfig, ProviderConfig`

```python
@dataclass(frozen=True)
class PSPConfig:
    """Explicit configuration for PSP. No defaults that move money."""
    tenant_id: UUID
    ledger: LedgerConfig
    funding_gate: FundingGateConfig
    providers: list[ProviderConfig]
    event_store: EventStoreConfig

@dataclass(frozen=True)
class LedgerConfig:
    """Ledger behavior configuration."""
    require_balanced_entries: bool = True
    allow_negative_balances: bool = False

@dataclass(frozen=True)
class FundingGateConfig:
    """Funding gate thresholds and behavior."""
    commit_gate_enabled: bool = True
    pay_gate_enabled: bool = True  # ALWAYS True in production
    reservation_ttl_hours: int = 48

@dataclass(frozen=True)
class ProviderConfig:
    """Payment provider configuration."""
    name: str
    provider_type: str  # "ach", "fednow", "wire"
    credentials: dict[str, str]  # Provider-specific
    sandbox: bool = False

@dataclass(frozen=True)
class EventStoreConfig:
    """Event store configuration."""
    retention_days: int | None = None  # None = forever
    enable_replay: bool = True
```

**Stability**: Config classes are stable. Fields may be added with defaults (minor). Required fields will not be added without major.

### 3. Provider Protocol

**Import path**: `from payroll_engine.psp.providers import PaymentRailProvider`

```python
class PaymentRailProvider(Protocol):
    """Protocol for payment rail implementations."""

    @property
    def name(self) -> str: ...

    @property
    def supported_rails(self) -> list[str]: ...

    def submit(
        self,
        instruction: PaymentInstruction,
    ) -> SubmitResult: ...

    def check_status(
        self,
        provider_reference: str,
    ) -> StatusResult: ...

    def cancel(
        self,
        provider_reference: str,
    ) -> CancelResult: ...

    def parse_webhook(
        self,
        payload: bytes,
        headers: dict[str, str],
    ) -> WebhookEvent: ...
```

**Stability**: Protocol methods are stable. New optional methods may be added (minor). Implementers should use `**kwargs` for forward compatibility.

### 4. Domain Events

**Import path**: `from payroll_engine.psp.events import DomainEvent, EventCategory`

```python
@dataclass(frozen=True)
class DomainEvent:
    """Immutable domain event."""
    event_id: UUID
    event_type: str
    timestamp: datetime
    tenant_id: UUID
    correlation_id: UUID | None
    causation_id: UUID | None
    payload: dict
    version: int = 1
```

**Stable Event Types** (will not be removed or renamed):

| Event Type | Category | Payload Fields |
|------------|----------|----------------|
| `PaymentInstructionCreated` | payment | instruction_id, amount, recipient_id |
| `PaymentSubmitted` | payment | instruction_id, provider_name, provider_ref |
| `PaymentAccepted` | payment | instruction_id, provider_ref |
| `PaymentSettled` | settlement | instruction_id, settlement_id, settled_at |
| `PaymentReturned` | settlement | instruction_id, return_code, return_reason |
| `PaymentFailed` | payment | instruction_id, error_code, error_message |
| `LedgerEntryPosted` | ledger | entry_id, debit_account, credit_account, amount |
| `LedgerEntryReversed` | ledger | entry_id, reversal_entry_id, reason |
| `ReservationCreated` | funding | reservation_id, account_id, amount |
| `ReservationReleased` | funding | reservation_id, release_type |
| `FundingBlocked` | funding | batch_id, reason, shortfall_amount |
| `LiabilityClassified` | liability | instruction_id, classification, responsible_party |

**Event Evolution Rules**:
- Event names are immutable (never renamed)
- Payload fields are additive only (new fields OK, removal = major)
- Breaking changes require new event name or `V2` suffix
- Version field indicates payload schema version

### 5. Result Types

**Import path**: `from payroll_engine.psp import CommitResult, ExecuteResult, IngestResult, CallbackResult, BalanceResult`

```python
@dataclass(frozen=True)
class CommitResult:
    batch_id: UUID
    reservation_ids: list[UUID]
    total_reserved: Decimal
    is_new: bool  # True if newly committed, False if idempotent duplicate

@dataclass(frozen=True)
class ExecuteResult:
    batch_id: UUID
    submitted_count: int
    failed_count: int
    instructions: list[InstructionResult]

@dataclass(frozen=True)
class IngestResult:
    matched_count: int
    unmatched_count: int
    duplicate_count: int
    unmatched_records: list[SettlementRecord]

@dataclass(frozen=True)
class CallbackResult:
    processed: bool
    instruction_id: UUID | None
    new_status: str | None
    is_new: bool  # True if state changed, False if idempotent

@dataclass(frozen=True)
class BalanceResult:
    account_id: UUID
    total: Decimal
    reserved: Decimal
    available: Decimal
    as_of: datetime
```

**Stability**: Result types are stable. Fields may be added (minor). Fields will not be removed without major.

### 6. CLI Commands

**Entry point**: `psp` or `python -m payroll_engine.psp.cli`

```bash
# Stable commands
psp health [--component {all,db,providers,events}]
psp metrics [--format {json,prometheus}]
psp balance --tenant-id UUID --account-id UUID
psp replay-events --tenant-id UUID [--since ISO] [--until ISO] [--dry-run]
psp export-events --tenant-id UUID --output FILE
psp schema-check [--database-url URL]
psp subscriptions --list
```

**Stability**: Command names and required flags are stable. New commands and optional flags may be added (minor).

---

## Module Optionality

PSP is designed with strict optionality. The core money-moving functionality has **zero optional dependencies**.

| Module | Install Extra | Config-Time | Can Move Money? | Authority Level |
|--------|---------------|-------------|-----------------|-----------------|
| `payroll_engine.psp` | *none* | Always available | **YES** | Full (ledger, gates, payments) |
| `payroll_engine.psp.ai` (rules) | *none* | `AdvisoryConfig(enabled=True)` | **NO** | Read-only advisory |
| `payroll_engine.psp.ai` (ML) | `[ai]` | `AdvisoryConfig(enabled=True, model_name="...")` | **NO** | Read-only advisory |
| `payroll_engine.psp.crypto` | `[crypto]` | *(future)* | **NO** | *(reserved)* |

### AI Advisory: Two-Tier System

**Tier 1 - Rules-Baseline (no extras needed):**

```python
from payroll_engine.psp.ai import AdvisoryConfig, ReturnAdvisor

# Works immediately - no [ai] extras required
config = AdvisoryConfig(enabled=True)  # model_name defaults to "rules_baseline"
```

**Tier 2 - ML Models (requires [ai] extras):**

```python
# First: pip install payroll-engine[ai]
from payroll_engine.psp.ai import AdvisoryConfig, is_ml_available

if is_ml_available():
    config = AdvisoryConfig(enabled=True, model_name="gradient_boost")
```

**Hard Fail on Misconfiguration**: If an ML model is requested but `[ai]` extras are not installed, you get a clear `AIMLDepsNotInstalledError` with install instructions and a suggestion to use `rules_baseline`.

**AI Advisory Constraints** (enforced, not documented):
- AI can **NEVER** move money
- AI can **NEVER** write to the ledger
- AI can **NEVER** override funding gates
- AI can **NEVER** decide settlement truth
- AI can **ONLY** emit advisory events for human/policy review

### Public AI Types

**Import path**: `from payroll_engine.psp.ai import ...`

| Type | Purpose | Stability |
|------|---------|-----------|
| `is_ai_available(model)` | Check if model can be used | Stable |
| `is_ml_available()` | Check if ML extras installed | Stable |
| `require_ai_deps(model)` | Raise if model deps missing | Stable |
| `AINotInstalledError` | Clear error for ML misconfiguration | Stable |
| `STDLIB_MODELS` | Set of models that need no extras | Stable |
| `AdvisoryConfig` | AI configuration (enabled=False default) | Stable |
| `ReturnAdvisor` | Return root-cause analysis | Stable |
| `FundingRiskAdvisor` | Funding risk prediction | Stable |
| `InsightGenerator` | Advisory learning loop | Stable |
| `CounterfactualSimulator` | Policy what-if analysis | Stable |
| `TenantRiskProfiler` | Tenant risk scoring | Stable |
| `RunbookAssistant` | Incident response assistance | Stable |

---

## Explicitly Internal (Not Stable)

The following are **internal implementation details**. They may change without notice:

### Services (Do Not Import Directly)

```python
# ❌ INTERNAL - will change
from payroll_engine.psp.services.ledger_service import LedgerService
from payroll_engine.psp.services.funding_gate_service import FundingGateService
from payroll_engine.psp.services.payment_service import PaymentService
from payroll_engine.psp.services.settlement_service import SettlementService
from payroll_engine.psp.services.liability_service import LiabilityService
```

**Why internal**: These are implementation details of the facade. Their interfaces, method signatures, and existence may change.

### Models (Schema May Change)

```python
# ❌ INTERNAL - schema may evolve
from payroll_engine.psp.models import PSPLedgerEntry, PaymentInstruction, ...
```

**Why internal**: Table schemas beyond documented event payloads are not guaranteed. We promise forward-compatible migrations, not schema stability.

### Utilities

```python
# ❌ INTERNAL - helpers may change
from payroll_engine.psp.utils import ...
from payroll_engine.psp.validators import ...
```

---

## What Breaks Semver

A **MAJOR** version bump is required for:

1. Removing or renaming any public class/method/command
2. Changing required parameters of public methods
3. Changing the meaning of existing event types
4. Removing fields from result types
5. Changing CLI command names or required flags
6. Breaking migrations (requiring manual intervention)

A **MINOR** version bump is required for:

1. Adding new public classes/methods/commands
2. Adding optional parameters to existing methods
3. Adding new event types
4. Adding fields to result types
5. Adding CLI flags

A **PATCH** version bump is for:

1. Bug fixes
2. Performance improvements
3. Internal refactors
4. Documentation updates

---

## Enforcement

### For Contributors

Before merging any PR:

1. Does it add a new public import? → Update this document
2. Does it change a method signature? → Check semver implications
3. Does it add/modify events? → Follow event evolution rules
4. Does it change CLI? → Update this document

### For Users

```python
# ✅ SAFE - uses public API
from payroll_engine.psp import PSP, PSPConfig
psp = PSP(session, config)
result = psp.commit_payroll_batch(batch)

# ❌ UNSAFE - uses internal service
from payroll_engine.psp.services.ledger_service import LedgerService
ledger = LedgerService(session)  # May break on any update
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2025-01-25 | Initial public API definition |
