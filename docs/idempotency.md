# PSP Idempotency Guide

## The Problem

When using `ON CONFLICT DO NOTHING`, a caller might assume "no error = success = action taken." This is wrong and dangerous:

```python
# WRONG: Assumes post succeeded
result = ledger.post_entry(...)
# No error thrown, but entry might NOT have been created!
emit_event(PaymentCreated(...))  # Wrong! Might emit duplicate event!
```

## The Solution: Created vs Already Exists

Every idempotent write operation in PSP returns a result with **explicit status**:

```python
@dataclass
class PostResult:
    entry_id: UUID
    is_new: bool  # TRUE = created, FALSE = already existed

# CORRECT: Check is_new before downstream actions
result = ledger.post_entry(...)
if result.is_new:
    emit_event(PaymentCreated(...))
    update_related_records(...)
else:
    # This was a duplicate request - do nothing
    log.info(f"Duplicate request, returning existing entry {result.entry_id}")
```

## Pattern: All Idempotent Writes

Every service that does idempotent writes follows this pattern:

### 1. LedgerService.post_entry

```python
result = ledger.post_entry(
    tenant_id=tenant_id,
    idempotency_key=f"pay:{pay_statement_id}",
    ...
)

if result.is_new:
    # Newly created - emit events, update caches
    emitter.emit(LedgerEntryPosted(...))
else:
    # Duplicate - just return the existing ID
    pass

return result.entry_id
```

### 2. PaymentOrchestrator.create_instruction

```python
result = orchestrator.create_instruction(
    tenant_id=tenant_id,
    idempotency_key=f"batch:{batch_id}:emp:{employee_id}",
    ...
)

if result.is_new:
    # Only submit to provider if newly created
    orchestrator.submit_payment(result.instruction_id)
    emitter.emit(PaymentInstructionCreated(...))
```

### 3. EventStore.append

```python
stored = store.append(event)  # Returns bool

if stored:
    # New event - notify subscribers
    notify_subscribers(event)
else:
    # Duplicate event - ignore
    pass
```

### 4. ReconciliationService.record_settlement

```python
result = reconciler.record_settlement(
    external_trace_id=trace_id,
    ...
)

if result.is_new:
    # New settlement - post ledger entries
    ledger.post_entry(...)
    emitter.emit(SettlementReceived(...))
else:
    # Already processed this trace_id
    pass
```

## Idempotency Key Patterns

### Good Keys

```python
# Unique per logical operation
f"ledger:{funding_request_id}"
f"payment:{batch_id}:{employee_id}:{purpose}"
f"settlement:{provider}:{trace_id}"
f"reversal:{original_entry_id}"
```

### Bad Keys

```python
# Too generic - might collide
f"payment:{employee_id}"  # Same employee in different batches!
f"entry:{amount}"         # Same amount != same entry!

# Too random - defeats idempotency
f"payment:{uuid4()}"      # New UUID each call - no idempotency!
```

## Testing Idempotency

Every idempotent operation should have tests like:

```python
def test_create_instruction_idempotent():
    """Same idempotency key returns existing, not new."""
    idk = "test_idem_key"

    # First call creates
    result1 = orchestrator.create_instruction(idempotency_key=idk, ...)
    assert result1.is_new is True

    # Second call returns existing
    result2 = orchestrator.create_instruction(idempotency_key=idk, ...)
    assert result2.is_new is False
    assert result2.instruction_id == result1.instruction_id

def test_events_only_emitted_on_new():
    """Events should only be emitted when is_new=True."""
    emitter = MockEmitter()

    result1 = create_with_events(idk="key1", emitter=emitter)
    assert len(emitter.events) == 1

    result2 = create_with_events(idk="key1", emitter=emitter)
    assert len(emitter.events) == 1  # Still 1, not 2!
```

## PSP Facade Handles This

The PSP Facade internally handles idempotency correctly:

```python
class PSP:
    def execute_payments(self, ...):
        for item in items:
            result = orchestrator.create_instruction(...)

            # Only emit and submit if newly created
            if result.is_new:
                emitter.emit(PaymentInstructionCreated(...))
                submit_result = orchestrator.submit_payment(...)
                if submit_result.success:
                    emitter.emit(PaymentSubmitted(...))
            # If duplicate, we just return the existing instruction
            # without double-emitting or double-submitting
```

**This is why using the facade is recommended** - it handles these edge cases.

## Audit: All Idempotent Paths

| Service | Method | Idempotency Key | is_new Check |
|---------|--------|-----------------|--------------|
| LedgerService | post_entry | tenant_id + idempotency_key | ✓ PostResult.is_new |
| LedgerService | get_or_create_account | tenant_id + le_id + type + currency | Returns ID (insert on conflict do nothing + select) |
| PaymentOrchestrator | create_instruction | tenant_id + idempotency_key | ✓ InstructionResult.is_new |
| ReconciliationService | record_settlement | trace_id unique constraint | ✓ Check rowcount |
| EventStore | append | event_id PK | ✓ Returns bool |
| LiabilityService | record_liability_event | tenant_id + idempotency_key | ✓ Returns existing or new ID |
| FundingGateService | create_reservation | No idempotency (by design) | N/A |

## Why Reservations Aren't Idempotent

Reservations intentionally lack idempotency because:

1. Each reservation is a distinct hold
2. Same batch might need multiple reservations
3. Caller must track reservation IDs explicitly

```python
# Multiple reservations for same batch are valid
res1 = gate.create_reservation(purpose="batch:123:taxes")
res2 = gate.create_reservation(purpose="batch:123:net_pay")
# Both are valid, distinct reservations
```

## Common Mistakes

### 1. Ignoring is_new

```python
# WRONG
result = ledger.post_entry(...)
do_downstream_work()  # Runs even on duplicate!

# RIGHT
result = ledger.post_entry(...)
if result.is_new:
    do_downstream_work()
```

### 2. Emitting Events Unconditionally

```python
# WRONG
result = orchestrator.create_instruction(...)
emitter.emit(PaymentCreated(...))  # Duplicate event on retry!

# RIGHT
result = orchestrator.create_instruction(...)
if result.is_new:
    emitter.emit(PaymentCreated(...))
```

### 3. Assuming Retry == Failure

```python
# WRONG: Treating duplicate as error
result = ledger.post_entry(...)
if not result.is_new:
    raise DuplicateError()  # Wrong! Duplicate is expected on retry

# RIGHT: Duplicate is success (idempotent)
result = ledger.post_entry(...)
# is_new=False is fine, caller gets the entry_id either way
return result.entry_id
```

## Summary

1. **Every idempotent write returns `is_new` (or equivalent)**
2. **Check `is_new` before downstream actions**
3. **Duplicate is not an error - it's expected behavior**
4. **Use the PSP Facade when possible - it handles this correctly**
