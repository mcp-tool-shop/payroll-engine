# Coder Instructions — PSP Build Pack (No Presentations)

This pack extends the Phase 1 payroll engine into a full PSP operations layer.
Focus: **ledger, funding, payments, reconciliation, and safe-to-commit controls**.

---
## 1) Implement PSP Ledger (specs/01)
### 1.1 Create tables
- Add the PSP ledger tables described in `specs/01_PSP_LEDGER_SCHEMA_AND_INVARIANTS.md`.
- Use append-only guarantees:
  - Disallow UPDATE/DELETE for posted ledger entries via triggers (allow reversals).

### 1.2 Enforce invariants
- Unique idempotency per tenant for ledger postings.
- Prevent reservations/outbound initiation when balances insufficient.
- Link outbound ledger entries to settlement events (eventually consistent).

### 1.3 Posting service
Implement a `LedgerService` with:
- `post_entry(idempotency_key, debit_account, credit_account, amount, source_type, source_id, metadata)`
- `reverse_entry(original_entry_id, reason)`
- `get_balance(account_id)`
- `create_reservation(reserve_type, amount, source_ref)` (all-or-nothing)

**All operations must be transactional and retry-safe.**

---
## 2) Implement Funding Ingestion
### 2.1 Funding request model
Create:
- `funding_request` (intent; idempotent)
- `funding_event` (actual; settlement/return)

Funding rails to support:
- ACH debit (primary)
- Wire (inbound)
- Reverse wire / drawdown wire (if available)
- Future: FedNow/RTP

### 2.2 Reconciliation loop
- Daily job to pull inbound settlement results from provider adapter.
- Create `psp_settlement_event` records (idempotent on external_trace_id).
- Post corresponding ledger entries.

---
## 3) Implement Payment Orchestration (specs/02)
### 3.1 Payment instruction model
- `payment_instruction` is business intent (idempotent).
- `payment_attempt` is provider-specific.
- `psp_settlement_event` is truth of settlement.

### 3.2 Provider adapters
Implement an interface like:
- `submit(instruction)`, `get_status`, `reconcile(date)`

Start with:
- ACH credit payments for employee net pay (NACHA)

---
## 4) Implement “Safe to Commit” Funding Gate (specs/03)
Decide policy:
- Strict (block commit without funds) OR Hybrid (allow commit, block pay)

Implement:
- funding requirement computation from payroll outputs (net, taxes, 3p)
- ledger availability checks
- reservation creation in a single transaction
- high-risk flags (cooldown for bank changes; spike detection)

---
## 5) Integration points with Phase 1 engine
**Never** change payroll math because of funding.
- From committed payroll, generate:
  - employee payment instructions
  - tax liabilities (phase 2B)
  - third-party obligations (phase 2B)

---
## 6) Test plan (minimum)
Add integration tests covering:
- idempotent ledger posting (retries produce no duplicates)
- reservation prevents overspend
- funding gate fails when funds not settled
- payment retries safe after partial settlement
- reconciliation matches bank settlement to ledger entries

---
## 7) Bank support: Amegy (Zions)
Implement adapters in a bank-agnostic way.
Capture bank-specifics via configuration:
- supported rails
- cutoffs
- file vs API submission
- reconciliation feeds
- trace id formats

Do not hardcode bank behavior in orchestration logic.

---
## Deliverables
- Schema migrations for PSP ledger + funding + payments
- Services: LedgerService, FundingService, PaymentOrchestrator
- Provider adapter skeletons: ACH + placeholder FedNow
- Integration tests proving safety under retries and partial failures


---
## Added in v2
- `migrations/201_psp_ledger_tables.sql` (FULL)
- `migrations/202_payments_instructions.sql` (FULL)
- `migrations/203_funding_requests.sql` (FULL)
- `src/psp_ops/` provider + service scaffolding
- `tests/psp_ops/` skeleton tests
