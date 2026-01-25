# PSP Invariants

This document declares the non-negotiable invariants that govern the PSP ledger, payment orchestration, and funding systems. These are not suggestions—they are the laws of the system. Code that violates them is broken by definition.

---

## 1. Ledger Invariants

### 1.1 Append-Only

```
INVARIANT: psp_ledger_entry is append-only.
ENFORCEMENT: Database triggers prevent UPDATE and DELETE.
CORRECTION: Create reversal entries to undo economic effects.
VIOLATION: Any UPDATE or DELETE to psp_ledger_entry is a critical incident.
```

### 1.2 Double-Entry Balance

```
INVARIANT: For any account at any point in time:
           balance = Σ(credits to account) - Σ(debits from account)

ENFORCEMENT: All postings require both debit_account_id and credit_account_id.
VERIFICATION: sum(amount) WHERE credit_account_id = X minus
              sum(amount) WHERE debit_account_id = X
              must equal reported balance.
```

### 1.3 Positive Amounts Only

```
INVARIANT: All ledger entry amounts are strictly positive (amount > 0).
ENFORCEMENT: CHECK constraint on psp_ledger_entry.amount.
RATIONALE: Negative amounts create ambiguity. Direction is encoded in
           debit/credit, not sign.
```

### 1.4 Idempotent Posting

```
INVARIANT: Given (tenant_id, idempotency_key), only one entry exists.
ENFORCEMENT: UNIQUE constraint + ON CONFLICT DO NOTHING.
BEHAVIOR: Retries with same key return existing entry, never duplicate.
```

### 1.5 Source Traceability

```
INVARIANT: Every ledger entry has a source_type and source_id.
RATIONALE: You must be able to explain why every penny moved.
EXAMPLES: source_type='funding_request', source_id=<UUID>
          source_type='payment_instruction', source_id=<UUID>
          source_type='psp_ledger_entry', source_id=<reversal target>
```

---

## 2. Balance Invariants

### 2.1 Non-Negative Available

```
INVARIANT: available_balance >= 0 after pay gate evaluation.
ENFORCEMENT: Pay gate hard-fails if required > available - reserved.
RATIONALE: You cannot disburse money you don't have.
```

### 2.2 Reservation Consistency

```
INVARIANT: Σ(active reservations) <= available_balance at time of creation.
ENFORCEMENT: Service layer validates before INSERT.
NOTE: Reservations do not move money; they prevent overspend.
```

### 2.3 Reservation Lifecycle

```
INVARIANT: Reservation status transitions are one-way:
           active -> released
           active -> consumed

ENFORCEMENT: Application logic + CHECK constraint.
VIOLATION: Released/consumed reservations reactivating is a bug.
```

---

## 3. Payment Invariants

### 3.1 Instruction-to-Debit Mapping

```
INVARIANT: Every payment instruction maps to exactly one funding source.
ENFORCEMENT: payment_instruction has legal_entity_id, which maps to
             client_funding_clearing account.
RATIONALE: Ambiguous funding sources cause settlement mismatches.
```

### 3.2 Status Progression

```
INVARIANT: Payment instruction status transitions follow this graph:

           created -> queued -> submitted -> accepted -> settled
                                    |            |
                                    v            v
                                  failed      reversed
                                    |
                                    v
                                 canceled

ENFORCEMENT: State machine in PaymentOrchestrator.
VIOLATION: Backward transitions (settled -> submitted) are bugs.
EXCEPTION: Reversal is a new state, not a rollback.
```

### 3.3 Attempt Idempotency

```
INVARIANT: (provider, provider_request_id) is unique across all attempts.
ENFORCEMENT: UNIQUE constraint on payment_attempt.
RATIONALE: Prevents double-submission to providers.
```

### 3.4 Settlement Truth

```
INVARIANT: psp_settlement_event is the source of truth for what happened.
           Payment instruction status reflects settlement, not vice versa.

ENFORCEMENT: Reconciliation job updates instruction status from settlement.
RATIONALE: Banks don't lie. Our records might.
```

---

## 4. Funding Gate Invariants

### 4.1 Two-Gate Model

```
INVARIANT: Commit gate is policy-driven (may soft-fail).
           Pay gate is absolute (always hard-fails on insufficient funds).

ENFORCEMENT: evaluate_commit_gate(strict=True/False)
             evaluate_pay_gate() # always strict

RATIONALE: Business may accept risk at commit; not at disbursement.
```

### 4.2 Evaluation Immutability

```
INVARIANT: Once persisted, funding_gate_evaluation cannot be modified.
ENFORCEMENT: Idempotency key prevents re-evaluation.
RATIONALE: Audit trail must show what was decided, not what we wish we decided.
```

### 4.3 Requirement Computation

```
INVARIANT: Funding requirement is computed from committed payroll outputs,
           not from funding requests or user estimates.

ENFORCEMENT: FundingGateService queries pay_statement.net_pay and
             pay_line_item totals.
RATIONALE: Payroll math is truth. Funding requests are intent.
```

---

## 5. Settlement Invariants

### 5.1 External Trace Uniqueness

```
INVARIANT: (psp_bank_account_id, external_trace_id) is unique.
ENFORCEMENT: UNIQUE constraint on psp_settlement_event.
RATIONALE: Banks don't reuse trace IDs. Duplicates mean replay or bug.
```

### 5.2 Status Progression

```
INVARIANT: Settlement event status transitions:

           created -> submitted -> accepted -> settled
                                      |
                                      v
                                   failed -> returned

           Additionally: settled -> reversed (post-settlement return)

ENFORCEMENT: Reconciliation service state machine.
```

### 5.3 Ledger Linkage

```
INVARIANT: Every settled psp_settlement_event should have a linked
           psp_ledger_entry via psp_settlement_link.

ENFORCEMENT: Reconciliation creates link when posting settlement entry.
VERIFICATION: Unlinked settlements are flagged in reconciliation reports.
```

### 5.4 Return Handling

```
INVARIANT: If settlement transitions from settled -> returned/reversed,
           the corresponding ledger entry MUST be reversed.

ENFORCEMENT: ReconciliationService._handle_status_change()
RATIONALE: Money that "settled" then "returned" never actually arrived.
```

---

## 6. Operational Invariants

### 6.1 Tenant Isolation

```
INVARIANT: All queries filter by tenant_id. No cross-tenant data access.
ENFORCEMENT: Every service method requires tenant_id parameter.
             Indexes include tenant_id as leading column.
VIOLATION: Cross-tenant data exposure is a critical security incident.
```

### 6.2 Audit Trail

```
INVARIANT: All state changes are traceable to:
           - timestamp
           - user or system actor
           - source event

ENFORCEMENT: created_at, created_by_user_id, source_type, source_id
             on all mutable records.
```

### 6.3 Idempotency Everywhere

```
INVARIANT: All external-facing operations are idempotent.
           Retrying with the same inputs produces the same result.

ENFORCEMENT: Idempotency keys on:
             - psp_ledger_entry
             - payment_instruction
             - funding_request
             - funding_gate_evaluation

RATIONALE: Networks fail. Webhooks replay. Users double-click.
```

---

## 7. Prohibited Operations

These operations are explicitly forbidden in production:

| Operation | Why |
|-----------|-----|
| DELETE FROM psp_ledger_entry | Destroys audit trail |
| UPDATE psp_ledger_entry SET amount = ... | Violates append-only |
| INSERT with negative amount | Ambiguous semantics |
| Cross-tenant balance transfer | Security violation |
| Pay gate bypass | Fiduciary breach |
| Manual status override without reversal | Hides economic truth |

---

## 8. Verification Queries

### 8.1 Ledger Balance Check

```sql
-- Verify balance computation for an account
WITH account_balance AS (
    SELECT
        (SELECT COALESCE(SUM(amount), 0) FROM psp_ledger_entry
         WHERE credit_account_id = :account_id AND tenant_id = :tenant_id) -
        (SELECT COALESCE(SUM(amount), 0) FROM psp_ledger_entry
         WHERE debit_account_id = :account_id AND tenant_id = :tenant_id) AS computed
)
SELECT computed FROM account_balance;
```

### 8.2 Unlinked Settlements

```sql
-- Find settlements without ledger links (potential reconciliation gaps)
SELECT se.*
FROM psp_settlement_event se
LEFT JOIN psp_settlement_link sl ON sl.psp_settlement_event_id = se.psp_settlement_event_id
WHERE sl.psp_settlement_link_id IS NULL
  AND se.status = 'settled';
```

### 8.3 Orphaned Reservations

```sql
-- Find active reservations older than 30 days (potential leaks)
SELECT * FROM psp_reservation
WHERE status = 'active'
  AND created_at < NOW() - INTERVAL '30 days';
```

### 8.4 Double-Entry Validation

```sql
-- Every entry should have distinct debit and credit accounts
SELECT * FROM psp_ledger_entry
WHERE debit_account_id = credit_account_id;
-- Result should be empty
```

---

## 9. Incident Response

When an invariant is violated:

1. **Stop the bleeding**: Halt affected payment processing
2. **Preserve evidence**: Do not "fix" data before investigation
3. **Trace the source**: Use idempotency_key and source_id to find root cause
4. **Correct via reversal**: Post reversal entries, never edit history
5. **Document**: Record incident, root cause, and correction in audit log

---

## 10. Change Protocol

To modify these invariants:

1. Propose change with rationale
2. Analyze impact on existing data
3. Write migration plan (always additive, never destructive)
4. Update this document
5. Update enforcement code
6. Deploy with feature flag
7. Verify invariants hold post-deployment

**These invariants are versioned with the codebase. They are law.**
