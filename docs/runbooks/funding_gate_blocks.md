# Runbook: Funding Gate Blocks

## Overview

The funding gate evaluates whether a payroll can proceed based on available funds and policy rules. A block means payroll cannot be committed until the issue is resolved.

## Gate Types

### Commit Gate
- Runs at payroll commit time
- Can be "soft" (warning) or "strict" (block)
- Evaluates: policy limits, credit utilization, historical patterns

### Pay Gate
- Runs at payment execution time
- ALWAYS strict - no bypass
- Evaluates: available_balance >= required_amount
- This is the "real money" check

## Symptoms

- `CommitResult.status` = `blocked_policy` or `blocked_funds`
- `GateResult.passed` = `False`
- `FundingBlocked` or `FundingInsufficientFunds` events
- Payroll stuck in "pending" state

## Investigation Queries

### 1. Check Current Balance

```sql
SELECT
    la.psp_ledger_account_id,
    la.name AS account_name,
    la.account_type,
    SUM(CASE WHEN e.credit_account_id = la.psp_ledger_account_id THEN e.amount ELSE 0 END) -
    SUM(CASE WHEN e.debit_account_id = la.psp_ledger_account_id THEN e.amount ELSE 0 END) AS total_balance
FROM psp_ledger_account la
LEFT JOIN psp_ledger_entry e ON e.debit_account_id = la.psp_ledger_account_id
                             OR e.credit_account_id = la.psp_ledger_account_id
WHERE la.tenant_id = :tenant_id
  AND la.legal_entity_id = :legal_entity_id
GROUP BY la.psp_ledger_account_id, la.name, la.account_type;
```

### 2. Check Active Reservations

```sql
SELECT
    br.psp_balance_reservation_id,
    br.amount,
    br.purpose,
    br.status,
    br.expires_at,
    br.created_at
FROM psp_balance_reservation br
WHERE br.tenant_id = :tenant_id
  AND br.account_id = :funding_account_id
  AND br.status = 'active'
  AND br.expires_at > NOW()
ORDER BY br.expires_at;
```

### 3. Calculate Available Balance

```sql
WITH account_balance AS (
    SELECT
        SUM(CASE WHEN e.credit_account_id = :account_id THEN e.amount ELSE 0 END) -
        SUM(CASE WHEN e.debit_account_id = :account_id THEN e.amount ELSE 0 END) AS total
    FROM psp_ledger_entry e
    WHERE e.tenant_id = :tenant_id
),
reserved_amount AS (
    SELECT COALESCE(SUM(amount), 0) AS reserved
    FROM psp_balance_reservation
    WHERE tenant_id = :tenant_id
      AND account_id = :account_id
      AND status = 'active'
      AND expires_at > NOW()
)
SELECT
    ab.total AS total_balance,
    ra.reserved AS reserved_amount,
    ab.total - ra.reserved AS available_balance
FROM account_balance ab, reserved_amount ra;
```

### 4. Check Recent Funding Requests

```sql
SELECT
    fr.psp_funding_request_id,
    fr.amount,
    fr.status,
    fr.block_reason,
    fr.created_at
FROM psp_funding_request fr
WHERE fr.tenant_id = :tenant_id
  AND fr.legal_entity_id = :legal_entity_id
ORDER BY fr.created_at DESC
LIMIT 10;
```

### 5. Check Gate Evaluations

```sql
SELECT
    ge.gate_evaluation_id,
    ge.gate_type,
    ge.passed,
    ge.reason,
    ge.required_amount,
    ge.available_balance,
    ge.policy_violated,
    ge.created_at
FROM psp_gate_evaluation ge
WHERE ge.tenant_id = :tenant_id
  AND ge.funding_request_id = :funding_request_id
ORDER BY ge.created_at DESC;
```

## Resolution Steps

### Block Reason: Insufficient Funds

1. **Verify the Shortfall**
   ```sql
   SELECT
       :required_amount AS needed,
       available_balance AS have,
       :required_amount - available_balance AS shortfall
   FROM (
       -- Use available balance query above
   );
   ```

2. **Options to Resolve**:

   | Option | Action | Time to Resolve |
   |--------|--------|-----------------|
   | Client funds account | Wire/ACH from client | 1-3 days |
   | Release expired reservations | Auto or manual | Immediate |
   | Split payroll | Process in batches | Immediate |
   | Credit extension | If policy allows | Requires approval |

3. **For Expired Reservations**:
   ```sql
   UPDATE psp_balance_reservation
   SET status = 'expired',
       updated_at = NOW()
   WHERE tenant_id = :tenant_id
     AND status = 'active'
     AND expires_at < NOW();
   ```

4. **For Client Funding**:
   ```python
   # Wait for incoming settlement, then retry
   result = psp.commit_payroll_batch(batch)
   ```

### Block Reason: Policy Violation

1. **Identify the Policy**
   - Check `GateResult.policy_violated`
   - Common policies:
     - `max_single_payment` - Single payment too large
     - `max_daily_volume` - Daily limit exceeded
     - `max_credit_utilization` - Using too much credit
     - `blocked_payee` - Payee on blocklist

2. **For `max_single_payment`**:
   - Split large payments into multiple smaller ones
   - Request policy exception (requires approval)

3. **For `max_daily_volume`**:
   - Wait until next day
   - Request temporary limit increase

4. **For `max_credit_utilization`**:
   - Client needs to fund account
   - Reduce credit line usage

5. **For `blocked_payee`**:
   - Investigate why payee is blocked
   - May need compliance approval to unblock

### Block Reason: Reservation Conflict

1. **Check What's Reserved**
   ```sql
   SELECT * FROM psp_balance_reservation
   WHERE tenant_id = :tenant_id
     AND account_id = :account_id
     AND status = 'active';
   ```

2. **If Reservation is Stale**:
   - Check if associated payroll was processed
   - Release reservation if no longer needed:
   ```python
   funding_gate.release_reservation(
       tenant_id=tenant_id,
       reservation_id=reservation_id,
   )
   ```

## Manual Override (Emergency Only)

**WARNING**: Manual overrides bypass safety checks. Use only with approval.

```python
# Emergency: Skip commit gate (still enforces pay gate)
config = PSPConfig(
    commit_gate_strict=False,  # Soft fail on commit
    pay_gate_always_enforced=True,  # Never skip this!
)
psp = PSP(session, config)

# This will proceed even if commit gate would block
# BUT pay gate will still block if truly insufficient funds
result = psp.commit_payroll_batch(batch)
```

**Never disable pay gate** - it's the last line of defense.

## Client Communication

| Block Reason | Message to Client |
|--------------|-------------------|
| Insufficient Funds | "Your account balance is insufficient. Please fund $X to proceed." |
| Daily Limit | "Daily payment limit reached. Payroll will process tomorrow." |
| Large Payment | "Payment exceeds single-transaction limit. Contact support." |
| Credit Limit | "Credit utilization at maximum. Please fund account." |

## Monitoring

Set up alerts for:

- Block rate > 5% of payroll attempts
- Same client blocked 3+ times in a week
- Any block > $100,000
- Reservation utilization > 80% of available balance

## Escalation

Escalate if:

- Client has funded but balance not reflecting
- Reconciliation lag causing stale balances
- Policy needs emergency adjustment
- Potential fraud indicators (rapid funding/withdrawal)

## Prevention

- Proactive balance monitoring (alert at 80% utilization)
- Pre-payroll balance checks (day before)
- Automatic reservation cleanup job
- Clear funding instructions to clients
