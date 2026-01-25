# PSP Threat Model

## Overview

This document identifies threats to the Payment Service Provider (PSP) system and documents mitigations. It follows the STRIDE methodology and focuses on practical, payroll-specific attack vectors.

## Assets Under Protection

| Asset | Value | Impact if Compromised |
|-------|-------|----------------------|
| **Funds in transit** | Direct financial loss | Unauthorized transfers, theft |
| **Ledger integrity** | Accounting accuracy | Incorrect balances, audit failures |
| **Tenant separation** | Customer trust | Data leakage, cross-tenant attacks |
| **Audit log** | Compliance | Repudiation, regulatory fines |
| **Employee PII** | Privacy | Identity theft, GDPR violations |
| **Bank credentials** | System access | Unauthorized submissions |

## Adversary Profiles

### A1: Malicious Client Admin

**Profile**: Authorized user at a client company with admin access to their tenant.

**Motivation**: Embezzlement, fraud, sabotage.

**Capabilities**:
- Can create payment instructions within their tenant
- Can view their tenant's ledger and events
- Cannot access other tenants
- Cannot modify system configuration

**Attack vectors**:
- Create fraudulent payments to accomplice accounts
- Manipulate timing to exploit float
- Delete audit trails (if possible)
- Social engineer support staff

### A2: Insider Engineer

**Profile**: Developer or operator with production access.

**Motivation**: Financial gain, coercion, disgruntlement.

**Capabilities**:
- Database read/write access
- Code deployment access
- Log access
- Provider credential access (potentially)

**Attack vectors**:
- Direct database manipulation
- Deploy malicious code
- Exfiltrate credentials
- Cover tracks in logs

### A3: Compromised Provider Webhook

**Profile**: Attacker who has compromised a payment provider or can spoof webhooks.

**Motivation**: Steal funds, disrupt operations.

**Capabilities**:
- Send arbitrary webhook payloads
- Replay legitimate webhooks
- Modify webhook contents

**Attack vectors**:
- Fake settlement confirmations
- Mark failed payments as successful
- Cause double-processing via replay

### A4: External Attacker

**Profile**: No authorized access, attacks from internet.

**Motivation**: Financial gain, data theft.

**Capabilities**:
- Network-level attacks
- API fuzzing
- Credential stuffing

**Attack vectors**:
- SQL injection
- Authentication bypass
- API abuse

## Threat Analysis (STRIDE)

### Spoofing

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Fake provider webhook | Settlement ingestion | HMAC signature verification | ✅ Required |
| Spoofed tenant context | All operations | tenant_id from auth token, not request | ✅ Enforced |
| Forged idempotency key | Payment creation | Keys scoped to tenant | ✅ Enforced |

### Tampering

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Modify ledger entry | Ledger | Entries are append-only, reversals create new entries | ✅ Enforced |
| Change payment amount | Payment instruction | Amount immutable after creation | ✅ DB constraint |
| Alter event history | Audit log | Events append-only, no UPDATE/DELETE | ✅ DB constraint |
| Modify status backwards | Payment status | Trigger validates transitions | ✅ DB trigger |

### Repudiation

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Deny creating payment | Audit | All operations emit domain events | ✅ Enforced |
| Deny approving batch | Audit | Approval captured in event payload | ✅ Enforced |
| Deny settlement match | Reconciliation | Match stored with both IDs | ✅ Enforced |

### Information Disclosure

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Cross-tenant data access | All queries | tenant_id in all WHERE clauses | ✅ Enforced |
| Ledger balance exposure | Account balances | Balance queries require tenant context | ✅ Enforced |
| PII in logs | Employee data | Structured logging, PII redaction | ⚠️ Partial |
| Provider credentials | Bank access | Credentials in secrets manager, not DB | ⚠️ Config-dependent |

### Denial of Service

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Flood payment creation | System | Rate limiting per tenant | ⚠️ App layer |
| Lock ledger accounts | Ledger | Optimistic locking, no long holds | ✅ Enforced |
| Exhaust event storage | Event store | Retention policy, archival | ⚠️ Operational |

### Elevation of Privilege

| Threat | Target | Mitigation | Status |
|--------|--------|------------|--------|
| Access other tenant | Data | tenant_id enforced at DB level | ✅ Enforced |
| Skip pay gate | Payments | Facade enforces gate, no bypass | ✅ Enforced |
| Approve own batch | Segregation | Approval requires different user | ⚠️ App layer |

## Critical Invariants

These invariants are enforced at the database level and cannot be violated regardless of application bugs:

```sql
-- 1. Money is always positive
CHECK (amount > 0)

-- 2. No self-transfer
CHECK (debit_account_id <> credit_account_id)

-- 3. Payment status only moves forward
TRIGGER validate_payment_instruction_status_transition

-- 4. Entry can only be reversed once
UNIQUE (reversed_by_entry_id)

-- 5. Events are append-only
REVOKE UPDATE, DELETE ON psp_domain_event

-- 6. Idempotency keys are unique per tenant
UNIQUE (tenant_id, idempotency_key)
```

## Attack Scenarios

### Scenario 1: Insider Creates Ghost Payments

**Attack**: Engineer inserts payment instructions directly into DB, bypassing application.

**Mitigations**:
1. All payments require valid `idempotency_key` (application generates)
2. All payments require valid `payroll_batch_id` with matching tenant
3. Ledger entries require matching debit/credit in same transaction
4. Event emission would be missing (detectable in audit)

**Detection**: Event store shows no `PaymentInstructionCreated` event.

**Residual risk**: MEDIUM - Requires both DB access AND knowledge of schema.

### Scenario 2: Webhook Replay Attack

**Attack**: Attacker captures legitimate settlement webhook, replays to double-credit.

**Mitigations**:
1. Provider request ID + transaction ID stored as unique key
2. Duplicate webhook returns "already processed"
3. HMAC signature includes timestamp (replay window)

**Detection**: Duplicate key violation in settlement table.

**Residual risk**: LOW - Idempotency fully prevents double-processing.

### Scenario 3: Tenant Boundary Breach

**Attack**: Client admin crafts API request with different tenant_id.

**Mitigations**:
1. `tenant_id` extracted from JWT, never from request body
2. All DB queries include `WHERE tenant_id = :tenant_id`
3. Foreign keys validate tenant consistency

**Detection**: Authorization failures in access logs.

**Residual risk**: LOW - Multiple layers of enforcement.

### Scenario 4: Settlement Mismatch Exploitation

**Attack**: Attacker sends fake settlement for non-existent payment, hoping to credit account.

**Mitigations**:
1. Settlement matching requires existing payment instruction
2. Unmatched settlements flagged for manual review
3. No automatic crediting without match

**Detection**: Unmatched settlement queue grows.

**Residual risk**: LOW - Unmatched items don't credit accounts.

### Scenario 5: Float Timing Attack

**Attack**: Client admin submits payroll, then cancels funding before settlement.

**Mitigations**:
1. Reservation system locks funds at commit
2. Pay gate checks reservation before execution
3. Insufficient funds blocks the batch

**Detection**: Funding gate failures in events.

**Residual risk**: LOW - Reservation system prevents this.

## Residual Risks

These risks are acknowledged but not fully mitigated in the current design:

| Risk | Severity | Rationale |
|------|----------|-----------|
| **Insider with DB + code access** | HIGH | Insider can bypass all controls. Mitigated by: access logging, code review, separation of duties. |
| **Provider compromise** | MEDIUM | If bank/processor is compromised, they could settle fraudulently. Mitigated by: reconciliation, daily audits. |
| **Key management** | MEDIUM | Webhook signing keys must be rotated and protected. Mitigated by: secrets manager, rotation policy. |
| **PII exposure in logs** | LOW | Some PII may appear in error messages. Mitigated by: log scrubbing, access controls. |

## Security Controls Summary

### Preventive Controls

| Control | Implementation |
|---------|---------------|
| Input validation | Pydantic models, SQL parameterization |
| Authentication | JWT tokens, tenant extraction |
| Authorization | tenant_id in all queries |
| Integrity | DB constraints, triggers |
| Idempotency | Unique keys, duplicate detection |

### Detective Controls

| Control | Implementation |
|---------|---------------|
| Audit logging | Domain events for all operations |
| Anomaly detection | Daily health summary, volume alerts |
| Reconciliation | Settlement matching, unmatched queue |
| Integrity checks | Ledger balance verification |

### Corrective Controls

| Control | Implementation |
|---------|---------------|
| Reversal mechanism | Ledger reversals, not modifications |
| Event replay | Rebuild state from events |
| Manual review queue | Unmatched settlements, flagged payments |

## Compliance Mapping

| Requirement | How PSP Addresses |
|-------------|-------------------|
| **SOC 2 - CC6.1** (Logical access) | tenant_id enforcement, JWT auth |
| **SOC 2 - CC6.7** (Transmission integrity) | HMAC webhooks, TLS |
| **PCI-DSS 10.2** (Audit trails) | Domain events, immutable log |
| **NACHA Rules** (Return handling) | Liability classification, reversal flow |

## Recommendations

### Immediate (Before Production)

1. **Enable PII redaction in logs** - Configure structured logging to mask SSN, account numbers
2. **Implement rate limiting** - Protect against DoS at API gateway
3. **Set up alerting** - Alert on: unmatched settlements, constraint violations, auth failures

### Short-term (First 90 Days)

1. **Penetration test** - Third-party assessment of API and webhook handling
2. **Chaos engineering** - Test failure modes (DB down, provider timeout)
3. **Incident response drill** - Practice the return handling runbook

### Long-term

1. **Hardware security module** - Store provider credentials in HSM
2. **Formal verification** - Prove critical invariants mathematically
3. **Bug bounty** - External security researcher program

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-25 | PSP Team | Initial threat model |
