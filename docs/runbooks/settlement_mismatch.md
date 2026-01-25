# Runbook: Settlement Mismatch

## Overview

A settlement mismatch occurs when settlement records from a provider cannot be matched to payment instructions in our system. This can indicate:

- Timing issues (settlement arrived before instruction was created)
- External trace ID mismatch
- Orphan settlements (payments made outside our system)
- Provider data errors

## Symptoms

- `ReconciliationResult.unmatched_trace_ids` is non-empty
- `psp_settlement_event` records with `payment_instruction_id = NULL`
- Alerts from reconciliation job
- Balance discrepancies in ledger accounts

## Types of Mismatches

### 1. Unmatched Outbound Settlement
Provider says they settled a payment, but we can't find the instruction.

**Severity**: High - We may have paid someone without authorization.

### 2. Unmatched Inbound Settlement
Funds arrived that we didn't expect.

**Severity**: Medium - Need to identify source and proper accounting.

### 3. Amount Mismatch
Settlement amount doesn't match instruction amount.

**Severity**: High - Fee discrepancy, partial settlement, or error.

### 4. Duplicate Settlement
Same trace ID settled twice.

**Severity**: Critical - Blocked by unique constraint, but investigate root cause.

## Investigation Queries

### 1. Find Unmatched Settlements

```sql
SELECT
    se.psp_settlement_event_id,
    se.external_trace_id,
    se.rail,
    se.direction,
    se.amount,
    se.status,
    se.effective_date,
    se.created_at
FROM psp_settlement_event se
WHERE se.payment_instruction_id IS NULL
  AND se.psp_bank_account_id IN (
      SELECT psp_bank_account_id
      FROM psp_bank_account
      WHERE tenant_id = :tenant_id
  )
ORDER BY se.created_at DESC
LIMIT 100;
```

### 2. Search for Possible Matches

```sql
-- Find instructions with similar amounts around same date
SELECT
    pi.payment_instruction_id,
    pi.amount,
    pi.status,
    pi.created_at,
    pa.provider_request_id,
    pa.provider_trace_id
FROM payment_instruction pi
LEFT JOIN payment_attempt pa ON pa.payment_instruction_id = pi.payment_instruction_id
WHERE pi.tenant_id = :tenant_id
  AND pi.amount = :settlement_amount
  AND pi.created_at BETWEEN :settlement_date - INTERVAL '7 days'
                       AND :settlement_date + INTERVAL '1 day'
ORDER BY ABS(EXTRACT(EPOCH FROM (pi.created_at - :settlement_date)));
```

### 3. Check for Trace ID Variations

```sql
-- Provider might use different trace ID format
SELECT
    pa.provider_request_id,
    pa.provider_trace_id,
    pi.payment_instruction_id,
    pi.amount,
    pi.status
FROM payment_attempt pa
JOIN payment_instruction pi ON pi.payment_instruction_id = pa.payment_instruction_id
WHERE pi.tenant_id = :tenant_id
  AND (
      pa.provider_request_id ILIKE '%' || :partial_trace || '%'
      OR pa.provider_trace_id ILIKE '%' || :partial_trace || '%'
  );
```

### 4. Check Event History

```sql
SELECT
    event_type,
    timestamp,
    payload
FROM psp_domain_event
WHERE tenant_id = :tenant_id
  AND (
      payload->>'external_trace_id' = :trace_id
      OR payload->>'provider_request_id' = :trace_id
  )
ORDER BY timestamp DESC;
```

## Resolution Steps

### For Unmatched Outbound Settlements

1. **Verify with Provider**
   - Contact provider to confirm the settlement
   - Get full trace details and original submission

2. **Search Extended Window**
   - Payment might be from previous day's batch
   - Check for timezone mismatches

3. **If Match Found**
   ```sql
   -- Link settlement to instruction
   UPDATE psp_settlement_event
   SET payment_instruction_id = :instruction_id,
       payment_attempt_id = :attempt_id,
       updated_at = NOW()
   WHERE psp_settlement_event_id = :settlement_id
     AND payment_instruction_id IS NULL;
   ```

4. **If No Match Found**
   - Create incident ticket
   - Flag settlement for manual review
   - Do NOT auto-post ledger entries

### For Unmatched Inbound Settlements

1. **Check if Expected**
   - Could be a client funding their account
   - Could be a return from previous payment

2. **Trace the Source**
   - Use provider tools to identify originator
   - Check against known client bank accounts

3. **If Legitimate Funding**
   ```python
   # Use the facade to properly record
   psp.ingest_settlement_feed(
       tenant_id=tenant_id,
       bank_account_id=bank_account_id,
       provider_name="ach",
       records=[settlement_record],
   )
   ```

4. **If Unknown Source**
   - Hold funds in suspense account
   - Create investigation ticket
   - May need to return funds

### For Amount Mismatches

1. **Check for Fees**
   ```sql
   SELECT
       pi.amount AS expected,
       se.amount AS settled,
       pi.amount - se.amount AS difference
   FROM payment_instruction pi
   JOIN psp_settlement_event se ON se.payment_instruction_id = pi.payment_instruction_id
   WHERE pi.payment_instruction_id = :instruction_id;
   ```

2. **If Fee Deduction**
   - Post fee entry to ledger
   - Update provider contract if unexpected fee

3. **If Partial Settlement**
   - Create follow-up payment for remainder
   - Investigate why partial

## Manual Matching Process

When automated matching fails:

```python
from payroll_engine.psp import ReconciliationService

reconciler = ReconciliationService(session, ledger)

# Manual match
reconciler.manual_match(
    settlement_event_id=settlement_id,
    payment_instruction_id=instruction_id,
    match_reason="Manual: Provider trace format changed",
    matched_by=operator_user_id,
)
```

## Ledger Implications

| Scenario | Ledger Action |
|----------|---------------|
| Match found | Post settlement entry |
| Match found with fee | Post settlement + fee entries |
| No match, outbound | Suspense account until resolved |
| No match, inbound | Suspense account until resolved |
| Duplicate attempt | Blocked by constraint |

## Suspense Account Handling

```sql
-- View items in suspense
SELECT
    se.*,
    ba.account_name
FROM psp_settlement_event se
JOIN psp_bank_account ba ON ba.psp_bank_account_id = se.psp_bank_account_id
WHERE se.payment_instruction_id IS NULL
  AND se.status = 'settled'
  AND se.created_at > NOW() - INTERVAL '30 days';

-- Move from suspense when resolved
-- Use LedgerService, not raw SQL
```

## Escalation

Escalate immediately if:

- Unmatched outbound > $10,000
- More than 10 unmatched in single batch
- Same trace ID appears twice (constraint blocked)
- Pattern suggests provider system issue

## Prevention

- Normalize trace ID formats before comparison
- Store both provider_request_id AND provider_trace_id
- Implement fuzzy matching for edge cases
- Monitor reconciliation match rates
- Alert on match rate drops
