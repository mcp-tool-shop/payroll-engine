# Runbook: Payment Returns

## Overview

A return occurs when a payment that was initially accepted is later rejected by the receiving bank or recipient. Returns are a normal part of payment operations but require careful handling to maintain ledger integrity.

## Symptoms

- Provider callback received with `status: returned`
- Settlement feed contains return records (negative amounts or return codes)
- `payment_instruction.status` = `returned`
- Return codes in `psp_settlement_event` (e.g., ACH R01-R99, FedNow RJCT)

## Common Return Codes

### ACH Returns (Most Common)

| Code | Reason | Liability | Action |
|------|--------|-----------|--------|
| R01 | Insufficient Funds | Employer | Retry or offset future payroll |
| R02 | Account Closed | Employer/Employee | Contact employee, update bank info |
| R03 | No Account/Unable to Locate | Employer/Employee | Contact employee, verify bank info |
| R04 | Invalid Account Number | Employer/Employee | Contact employee, correct account |
| R08 | Payment Stopped | Recipient | Contact employee |
| R09 | Uncollected Funds | Employer | Wait and retry |
| R10 | Customer Advises Not Authorized | Disputed | Investigate, may require clawback |
| R16 | Account Frozen | Employer | Contact employee |
| R20 | Non-Transaction Account | Employer/Employee | Contact employee, use different account |

### FedNow Rejections

| Code | Reason | Liability |
|------|--------|-----------|
| RJCT | Rejected | Provider/Bank |
| AM04 | Insufficient Funds | Employer |
| AC04 | Closed Account | Employer/Employee |
| AC06 | Blocked Account | Employer |

## Investigation Queries

### 1. Find the Return Event

```sql
SELECT
    pi.payment_instruction_id,
    pi.status,
    pi.amount,
    pi.payee_type,
    pi.payee_ref_id,
    pa.provider_request_id,
    pa.provider_response,
    se.external_trace_id,
    se.return_code,
    se.return_reason
FROM payment_instruction pi
LEFT JOIN payment_attempt pa ON pa.payment_instruction_id = pi.payment_instruction_id
LEFT JOIN psp_settlement_event se ON se.payment_instruction_id = pi.payment_instruction_id
WHERE pi.payment_instruction_id = :instruction_id
  AND pi.tenant_id = :tenant_id;
```

### 2. Check Liability Classification

```sql
SELECT
    le.liability_event_id,
    le.error_origin,
    le.liability_party,
    le.recovery_path,
    le.recovery_status,
    le.amount,
    le.return_code,
    le.return_reason
FROM liability_event le
WHERE le.payment_instruction_id = :instruction_id
  AND le.tenant_id = :tenant_id
ORDER BY le.created_at DESC;
```

### 3. Check if Reversal Was Posted

```sql
SELECT
    e.psp_ledger_entry_id,
    e.entry_type,
    e.amount,
    e.source_id,
    e.created_at
FROM psp_ledger_entry e
WHERE e.source_type = 'psp_settlement_event'
  AND e.source_id IN (
      SELECT psp_settlement_event_id
      FROM psp_settlement_event
      WHERE payment_instruction_id = :instruction_id
  )
  AND e.entry_type = 'reversal';
```

### 4. Verify Ledger Balance

```sql
SELECT
    la.name AS account_name,
    SUM(CASE WHEN e.credit_account_id = la.psp_ledger_account_id THEN e.amount ELSE 0 END) -
    SUM(CASE WHEN e.debit_account_id = la.psp_ledger_account_id THEN e.amount ELSE 0 END) AS balance
FROM psp_ledger_entry e
JOIN psp_ledger_account la ON la.psp_ledger_account_id IN (e.debit_account_id, e.credit_account_id)
WHERE e.tenant_id = :tenant_id
  AND e.legal_entity_id = :legal_entity_id
GROUP BY la.psp_ledger_account_id, la.name;
```

## Resolution Steps

### Step 1: Verify Return is Recorded

1. Check `psp_settlement_event` for the return record
2. Verify `payment_instruction.status` = `returned`
3. Confirm `liability_event` exists with correct classification

### Step 2: Post Reversal Entry (If Not Done)

If the reconciliation didn't auto-post the reversal:

```sql
-- Find the original debit entry
SELECT * FROM psp_ledger_entry
WHERE source_type = 'payment_instruction'
  AND source_id = :instruction_id
  AND entry_type = 'payment_debit';

-- Post reversal manually (use service, not raw SQL)
-- This is just to understand what needs to happen
INSERT INTO psp_ledger_entry (
    tenant_id, legal_entity_id, entry_type,
    debit_account_id, credit_account_id, amount,
    source_type, source_id, idempotency_key
) VALUES (
    :tenant_id, :legal_entity_id, 'reversal',
    :original_credit_account,  -- Reverse the accounts
    :original_debit_account,
    :amount,
    'psp_settlement_event', :settlement_event_id,
    'reversal:' || :instruction_id
);
```

### Step 3: Classify Liability (If Not Done)

```python
from payroll_engine.psp import LiabilityService

liability = LiabilityService(session)
classification = liability.classify_return(
    rail="ach",
    return_code="R01",
    amount=Decimal("2500.00"),
    context={"employee_id": "...", "pay_period": "..."}
)

liability.record_liability_event(
    tenant_id=tenant_id,
    payment_instruction_id=instruction_id,
    error_origin=classification.error_origin.value,
    liability_party=classification.liability_party.value,
    recovery_path=classification.recovery_path.value,
    amount=amount,
    return_code="R01",
    return_reason="Insufficient Funds",
    idempotency_key=f"return:{instruction_id}:R01",
)
```

### Step 4: Initiate Recovery

Based on `recovery_path`:

| Path | Action |
|------|--------|
| `offset_future` | Add to next payroll as deduction |
| `clawback` | Initiate reversal payment |
| `write_off` | Mark as loss, escalate to finance |
| `insurance` | File insurance claim |
| `dispute` | Escalate to legal/compliance |

### Step 5: Notify Stakeholders

- **Employee**: If employee bank info issue
- **Employer**: If funds issue or requires action
- **Finance**: If write-off required
- **Compliance**: If disputed or suspicious

## Automation

The PSP Facade handles most returns automatically:

```python
from payroll_engine.psp.psp import PSP

psp = PSP(session, config)

# This handles: status update, reversal, liability, events
result = psp.handle_provider_callback(
    tenant_id=tenant_id,
    provider_name="ach_stub",
    callback_type="return",
    payload={
        "provider_request_id": "ACH123456",
        "status": "returned",
        "return_code": "R01",
        "return_reason": "Insufficient Funds",
        "amount": "2500.00",
    },
)
```

## Escalation

Escalate to engineering if:

- Reversal entry cannot be posted (ledger constraint violation)
- Duplicate returns for same payment
- Return code not in reference table
- Liability classification unclear

## Prevention

- Validate bank accounts with micro-deposits before first payment
- Use NACHA prenote for ACH
- Monitor return rates by employer (high rates = credit risk)
- Implement bank account velocity checks (too many changes = fraud indicator)
