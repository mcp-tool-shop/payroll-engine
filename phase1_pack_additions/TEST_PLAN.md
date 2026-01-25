# TEST_PLAN — Phase 1 Payroll Engine

This test plan is designed so a coder can validate Phase 1 end-to-end using Postgres + their service.

## Pre-req
- Postgres running
- Apply `ddl.sql`
- Apply all migrations in `migrations/` in order
- Load fixtures: `fixtures/seed_minimal.sql`

## 1) Schema sanity checks
1. Ensure extensions exist:
   - `pgcrypto`, `btree_gist`
2. Ensure exclusion constraints created (employment, employee deductions, tax profile, etc.)
3. Ensure unique constraints:
   - pay_statement_one_per_pre
   - pli_line_hash_unique
   - payment_batch_one_per_run

## 2) Preview flow
**Goal:** engine can compute deterministic preview for pay_run `pr` (fixtures).

Steps:
1. Set pay_run to `preview` (or call preview API).
2. Run `preview_pay_run(pay_run_id)` twice.
Expected:
- Same results each time.
- Per employee:
  - `pay_run_employee.gross` and `net` set.
- Earnings lines created in memory or preview tables (depending on design).
- No committed `pay_statement` unless you intentionally persist previews.

## 3) Approval locks
**Goal:** once approved, inputs cannot change.

Steps:
1. Call `approve_pay_run(pay_run_id)`.
2. Attempt UPDATE on a `time_entry` in scope:
   - change hours on `te1`.
Expected:
- Rejected at app layer (preferred) and/or DB layer.
- Audit event logged (optional but recommended).

## 4) Commit + idempotency (crash-safe)
**Goal:** commit is safe under retries.

Steps:
1. Call `commit_pay_run(pay_run_id)`.
Expected:
- pay_run.status becomes `committed`.
- One pay_statement per pay_run_employee.
- Pay_line_items exist for:
  - earnings
  - deductions
  - taxes (if implemented)
  - employer taxes (if implemented)
- Net pay equals sum(lines) exactly.

Retry safety:
2. Call `commit_pay_run(pay_run_id)` again.
Expected:
- No new pay_statements.
- No duplicate line items (unique line_hash index prevents it).
- Status remains committed.

Crash simulation (manual):
3. In code, artificially crash after committing first employee statement.
4. Restart and rerun commit.
Expected:
- Second run continues and finishes without duplicates.

## 5) Immutability
**Goal:** cannot mutate payroll artifacts after commit.

Steps:
1. Attempt UPDATE on pay_statement.net_pay for a committed run.
2. Attempt DELETE on pay_line_item.
Expected:
- Trigger blocks with a clear exception.

## 6) Reopen + change + reapprove
**Goal:** reopening creates new preview identity; commit requires reapproval.

Steps:
1. Transition `approved → preview` (reopen).
2. Modify time entry hours (now allowed).
3. Preview again: outputs change deterministically.
4. Approve again; commit again should be blocked if already committed unless void/reissue path implemented.
Expected:
- Either disallow reopen after committed (recommended) OR require void/reissue mechanism.

## 7) Void/reissue (if implemented in Phase 1)
**Goal:** void produces reversals and preserves audit trail.

Steps:
1. Void a pay_statement.
2. Create a reissue off-cycle run.
Expected:
- Original remains; reversal lines negate original.
- New statement has delta lines.

## 8) Golden rules validation
- No floating point usage
- All money in NUMERIC
- All lines follow sign conventions
- Every tax line references jurisdiction + rule_version_id
