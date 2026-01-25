# 01 — PSP Ledger Schema + Invariants (Build Pack)

**Scope:** Financial sub-ledger for a payroll service provider (PSP). This ledger tracks *client money movement* and obligations
(net pay, tax impound, third-party payables) without mutating payroll history.

## Design Principles (Non-Negotiable)
- **Payroll calculation never moves money.**
- **Money movement never rewrites payroll results.**
- Ledger is **append-only** (no UPDATE/DELETE for posted entries; use reversals).
- Every dollar is:
  - attributable (who/what/why)
  - reconcilable (to bank settlement)
  - auditable (actor + timestamp + source artifacts)

## Core Concepts
- **PSP Account:** A logical ledger account (client operating, tax impound, third-party payable bucket). Not necessarily a bank account.
- **Bank Account:** Actual settlement account at bank/processor (tokenized ref).
- **Ledger Entry:** Append-only posting (debit/credit or signed amount) referencing sources and settlement events.
- **Reservation:** Funds held for specific obligations (e.g., net pay batch, tax due).
- **Settlement:** Bank-confirmed movement (ACH, wire, RTP/FedNow) tied to ledger entries.

## Minimal Tables (Phase 2 Build)

### 1) psp_bank_account
Stores settlement accounts (PSP-owned accounts) and metadata.
- `psp_bank_account_id` (uuid pk)
- `tenant_id` (uuid) — owning tenant (PSP org / platform tenant)
- `bank_name` (text)
- `bank_account_ref_token` (text) — tokenized reference (no raw account numbers)
- `rail_support_json` (jsonb) — `{ "ach_credit":true, "ach_debit":true, "wire":true, "fednow":false, "rtp":false }`
- `status` (text) — active/disabled
- `created_at`

### 2) psp_ledger_account
Logical ledger accounts (client buckets). Typically created per legal_entity.
- `psp_ledger_account_id` (uuid pk)
- `tenant_id` (uuid) — client tenant
- `legal_entity_id` (uuid)
- `account_type` (text) CHECK IN:
  - `client_funding_clearing` (incoming funds awaiting allocation)
  - `client_net_pay_payable`
  - `client_tax_impound_payable`
  - `client_third_party_payable`
  - `psp_fees_revenue` (optional)
  - `psp_settlement_clearing` (bridge to bank)
- `currency` (char3, USD)
- `status` (active/closed)
- `created_at`
- UNIQUE(tenant_id, legal_entity_id, account_type, currency)

### 3) psp_ledger_entry (append-only postings)
- `psp_ledger_entry_id` (uuid pk)
- `tenant_id` (uuid) — client tenant context (who the money belongs to)
- `legal_entity_id` (uuid)
- `posted_at` (timestamptz)
- `entry_type` (text) CHECK IN:
  - `funding_received`
  - `funding_returned`
  - `reserve_created`
  - `reserve_released`
  - `employee_payment_initiated`
  - `employee_payment_settled`
  - `employee_payment_failed`
  - `tax_payment_initiated`
  - `tax_payment_settled`
  - `third_party_payment_initiated`
  - `third_party_payment_settled`
  - `fee_assessed`
  - `reversal`
- `debit_account_id` (uuid fk psp_ledger_account)
- `credit_account_id` (uuid fk psp_ledger_account)
- `amount` (numeric(14,4)) — positive amount for the posting (direction is via debit/credit)
- `source_type` (text) — e.g. `pay_run`, `pay_statement`, `tax_liability`, `third_party_obligation`, `funding_request`
- `source_id` (uuid)
- `correlation_id` (uuid) — ties multiple entries in one business action
- `idempotency_key` (text) — required for externally triggered postings
- `metadata_json` (jsonb)
- `created_by_user_id` (uuid nullable)
- UNIQUE(tenant_id, idempotency_key)

**Note:** Use double-entry style (debit/credit) so balances are provable and auditable.

### 4) psp_reservation
Holds funds for obligations without moving money externally.
- `psp_reservation_id` (uuid pk)
- `tenant_id`, `legal_entity_id`
- `reserve_type` (text) CHECK IN: `net_pay`, `tax`, `third_party`, `fees`
- `amount` (numeric)
- `currency`
- `status` (text) CHECK IN: `active`, `released`, `consumed`
- `source_type`, `source_id` — typically pay_run / tax filing period
- `created_at`, `released_at`

### 5) psp_settlement_event
Bank/processor settlement results (what actually happened).
- `psp_settlement_event_id` (uuid pk)
- `psp_bank_account_id`
- `rail` (text) CHECK IN: `ach`, `wire`, `rtp`, `fednow`, `check`, `internal`
- `direction` (text) CHECK IN: `inbound`, `outbound`
- `amount` (numeric)
- `currency`
- `status` (text) CHECK IN: `created`, `submitted`, `accepted`, `settled`, `failed`, `reversed`
- `external_trace_id` (text) — ACH trace, wire IMAD/OMAD, RTP message id, FedNow id
- `effective_date` (date)
- `raw_payload_json` (jsonb) — redacted/tokenized
- `created_at`

### 6) psp_settlement_link
Link settlement events to ledger entries (many-to-many).
- `psp_settlement_link_id` (uuid pk)
- `psp_settlement_event_id` (uuid fk)
- `psp_ledger_entry_id` (uuid fk)
- UNIQUE(psp_settlement_event_id, psp_ledger_entry_id)

### 7) tax_liability (derived from payroll; required for PSP)
Create per agency/jurisdiction liabilities from committed payroll line items.
- `tax_liability_id` (uuid pk)
- `tenant_id`, `legal_entity_id`
- `jurisdiction_id`, `tax_agency_id`
- `tax_type` (text) — FIT, SS, MED, FUTA, SUTA, SIT, local, etc.
- `period_start`, `period_end`, `due_date`
- `amount` (numeric)
- `status` (text) CHECK IN: `open`, `reserved`, `paid`, `amended`, `voided`
- `source_pay_run_id` (uuid)
- `created_at`
- Indexes: (legal_entity_id, due_date), (tax_agency_id, period_end)

### 8) third_party_obligation
For 401k, HSA, garnishments, union dues, etc.
- `third_party_obligation_id` (uuid pk)
- `tenant_id`, `legal_entity_id`
- `obligation_type` (text) — `garnishment`, `retirement`, `hsa`, `union`, `loan`, etc.
- `payee_profile_json` (jsonb) — routing metadata/token refs
- `amount` (numeric)
- `due_date`
- `status` (open/reserved/paid/failed)
- `source_pay_run_id`, `source_pay_statement_id` (nullable)
- `created_at`

## Ledger Invariants (Must Enforce)
1. **Append-only:** posted entries never updated/deleted. Use reversal entries.
2. **Idempotency:** all externally triggered postings require `idempotency_key` unique per tenant.
3. **No commingling:** each `psp_ledger_account` is tied to a (tenant, legal_entity, account_type).
4. **Balance safety:** you may not create a reservation or initiate outbound payment if available balance < required.
5. **Traceability:** every outbound payment entry must link to a settlement event (eventually).
6. **Reconciliation:** bank statement lines must map to settlement events; settlement events must map to ledger entries.
7. **Separation of concerns:** payroll tables remain immutable and do not store PSP ledger amounts (references only).

## Minimal Posting Flows (Canonical)

### A) Prefund net pay + taxes
1) Create funding_request (ACH debit)
2) On inbound settlement:
   - DR `psp_settlement_clearing`, CR `client_funding_clearing`
3) Allocate funds:
   - DR `client_funding_clearing`, CR `client_net_pay_payable`
   - DR `client_funding_clearing`, CR `client_tax_impound_payable`
   - DR `client_funding_clearing`, CR `client_third_party_payable`
4) Create reservations for the pay_run (net/tax/3p)
5) Initiate employee payments:
   - DR `client_net_pay_payable`, CR `psp_settlement_clearing`
6) On settlement:
   - record settlement event + link to ledger entry
7) Initiate tax remittance later:
   - DR `client_tax_impound_payable`, CR `psp_settlement_clearing`

### B) Net-only funding (tax impound later)
- Same, but reserve/collect only net now; schedule tax funding later based on due dates.

## Required Reports
- Client balance by bucket (net/tax/3p)
- Aging liabilities (tax + third-party)
- Settlement reconciliation (bank → settlement events → ledger entries)
- “Ready to pay” report for upcoming due dates
