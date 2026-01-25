# Architecture (High-Level) — Payroll Engine + PSP Ops

## Layers
1) Payroll Engine (Phase 1)
   - deterministic calculation
   - immutable statements/line items
   - state machine: draft/preview/approved/committed/paid/voided

2) PSP Ops (Phase 2)
   - PSP ledger (client funds + obligations)
   - funding ingestion (ACH debit/wire/etc.)
   - payment orchestration (employee/tax/third party)
   - settlement reconciliation loop
   - tax liability + filing workflows (later module)

## Key separations
- Payroll results do not move money.
- Money movement does not rewrite payroll.
- Filing does not equal payment; both reconcile to liabilities.

## Idempotency strategy
- Payroll: (statement unique per run_employee) + (line_hash unique per calculation)
- PSP: all postings and payment instructions require idempotency keys

## Where to start
- Implement PSP ledger schema + posting flows
- Implement funding requests + inbound settlement reconciliation
- Implement employee payment instruction → settlement → ledger linking
