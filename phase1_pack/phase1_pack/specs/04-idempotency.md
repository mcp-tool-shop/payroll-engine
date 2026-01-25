# 04 — Idempotency + Retries Contract (Phase 1)

## Invariants
1) Commit a pay run at most once.
2) Create one pay_statement per pay_run_employee.
3) Insert line items at most once per calculation identity.
4) Retries safe at any point.

## Calculation identity
Fingerprint:
- pay_run_id, employee_id, as_of_date
- engine_version
- inputs_fingerprint (hash of read set affecting pay)
- rules_fingerprint (hash of rule_version_ids used)

## Required DB constraints (recommend)
- `UNIQUE(pay_run_employee_id)` on `pay_statement`
- Dedupe line items with `calculation_id` + `line_hash` unique index:
  - `UNIQUE(pay_statement_id, calculation_id, line_hash)`

## Transaction boundaries
- One transaction per employee statement commit:
  - ensure statement exists
  - insert line items
  - update run_employee totals
- One final transaction to set pay_run status committed.

## Concurrency control
- Use advisory lock per pay_run_id during commit.
- Conditional update:
  `UPDATE pay_run SET status='committed' WHERE pay_run_id=? AND status='approved'`

## Payment batch idempotency
- `UNIQUE(pay_run_id, processor)` on payment_batch
- `UNIQUE(payment_batch_id, pay_statement_id)` already present

## Retry behavior
- Re-run safely: existing statements/line items are skipped by unique constraints.
- Partial failure: continue remaining employees; block run commit until errors resolved/excluded.
