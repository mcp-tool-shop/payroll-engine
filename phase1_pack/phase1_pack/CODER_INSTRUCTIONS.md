# Phase 1 — Coder Instructions (Implementation Guide)

This pack defines **Phase 1** for a US payroll SaaS platform:
- Postgres schema (DDL)
- Pay-run state machine + locking contract
- Line-item semantics
- Engine read/write contract
- Idempotency + retries contract

Your job is to implement the engine so it is:
- **correct** (deterministic; sums reconcile; auditable)
- **safe** (no silent drift; no double-pay on retries)
- **extensible** (new taxes/earnings/deductions without refactors)

---

## 0) Setup / environment
- Postgres 14+ recommended.
- Enable extensions: `pgcrypto`, `btree_gist`.
- Apply `/ddl.sql` to a fresh database.

---

## 1) REQUIRED schema deltas (do these first)
The DDL skeleton is intentionally minimal. Add these to support safe commit:

### 1.1 One statement per run_employee
```sql
ALTER TABLE pay_statement
  ADD CONSTRAINT pay_statement_one_per_pre UNIQUE (pay_run_employee_id);
```

### 1.2 Idempotent line items
Add columns:
- `pay_statement.calculation_id UUID NOT NULL`
- `pay_line_item.calculation_id UUID NOT NULL`
- `pay_line_item.line_hash TEXT NOT NULL`

Add unique index:
```sql
CREATE UNIQUE INDEX pli_line_hash_unique
  ON pay_line_item(pay_statement_id, calculation_id, line_hash);
```

### 1.3 Payment batch idempotency
```sql
ALTER TABLE payment_batch
  ADD CONSTRAINT payment_batch_one_per_run UNIQUE (pay_run_id, processor);
```

> If you want to keep preview artifacts separate, add preview tables or an `is_preview` column.

---

## 2) State machine enforcement
Implement transitions exactly:
- `draft → preview → approved → committed → paid`
- allow reopen: `approved → preview`
- support `voided` with reversal mechanics

### 2.1 Locks at `approved`
Phase 1 MVP locking approach:
- Add a `locked_by_pay_run_id UUID` + `locked_at` column to:
  - `time_entry`, `pay_input_adjustment`
- When run is approved:
  - mark all in-scope time/adjustments as locked by that run
  - reject edits to locked rows

Optionally record snapshot hashes in an `pay_run_lock` table for config rows.

### 2.2 Immutable results at `committed`
Enforce with both:
- application checks
- Postgres trigger preventing UPDATE/DELETE on `pay_statement` / `pay_line_item` when parent pay_run.status in ('committed','paid')

---

## 3) Engine interface (minimal)
Implement a service/module with these operations:

### 3.1 `preview_pay_run(pay_run_id) -> result`
- Valid statuses: draft/preview/approved
- For each included employee:
  - compute gross/net and either persist preview artifacts or return in response
- Must never mutate committed artifacts

### 3.2 `approve_pay_run(pay_run_id)`
- Transition to approved
- Lock all in-scope inputs
- Record audit event

### 3.3 `commit_pay_run(pay_run_id)`
- Acquire advisory lock: `pg_advisory_lock(hashtext(pay_run_id::text))`
- Validate:
  - status = approved
  - locks intact
  - no included employee has status=error
- Commit each employee statement idempotently (see section 5)
- Finalize run status with conditional UPDATE

---

## 4) Calculation pipeline rules (must follow order)
Per employee:
1) Build earnings line candidates from `time_entry` and earning adjustments
2) Apply pre-tax deductions
3) Compute employee taxes (per jurisdiction)
4) Apply post-tax deductions
5) Apply garnishments (priority order)
6) Compute employer taxes (liability only)
7) Rounding reconciliation (explicit rounding line if needed)
8) Validate net = sum(lines) exactly
9) Persist statement + line items

### 4.1 Sign conventions (do not deviate)
- earnings/reimbursements positive
- employee deductions/taxes negative
- employer taxes positive
Net excludes employer taxes.

### 4.2 Rate selection
For each time_entry:
- if `rate_override` present: use it
- else select from `pay_rate` using:
  - matching dims (job/project/department/worksite)
  - most specific match wins
  - priority tie-breaker

If missing rate -> error.

---

## 5) Idempotent persistence (core of safety)
### 5.1 Calculation identity
Create `calculation_id` for each employee commit based on:
- engine_version
- inputs_fingerprint
- rules_fingerprint
Store on `pay_statement` and on each `pay_line_item`.

### 5.2 Statement insert
```sql
INSERT INTO pay_statement (pay_run_employee_id, check_date, payment_method, net_pay, calculation_id)
VALUES (...)
ON CONFLICT (pay_run_employee_id) DO NOTHING;
```
Then SELECT the statement row. If it exists with different calculation_id -> stop; requires reopen/void path.

### 5.3 Line insert
Compute `line_hash` per line (hash of canonical JSON of defining fields).
Insert with:
```sql
INSERT INTO pay_line_item (..., calculation_id, line_hash)
VALUES (...)
ON CONFLICT DO NOTHING;
```

### 5.4 Run finalization
After all employees:
```sql
UPDATE pay_run
SET status='committed', committed_at=now()
WHERE pay_run_id=$1 AND status='approved';
```
If 0 rows updated: treat as already committed or invalid state.

---

## 6) Testing requirements (minimum)
Create automated tests for:
- Determinism: same inputs => identical line items + totals
- Rounding: pennies reconcile using rounding adjustment line
- Locks: editing locked input fails after approval
- Retry safety: crash mid-commit then retry produces no duplicates
- Voids: reversal statement negates original totals

Add a small fixture dataset:
- 2 employees, multi-rate, one pre-tax deduction, one post-tax deduction
- at least one state tax profile
- off-cycle adjustment

---

## 7) Implementation milestones
1) Apply DDL + required deltas (section 1)
2) Build minimal admin CRUD for:
   - tenant/legal_entity
   - employees/employment
   - earning/deduction codes
   - tax profiles
   - pay periods + pay runs
3) Implement preview calculation (earnings only)
4) Add deductions + taxes + employer taxes
5) Add approval locks
6) Add commit + idempotency
7) Add payments batch generation (stub processor ok)
8) Add GL export generation (CSV ok)

---

## 8) “Do not do” list (serious)
- Do not overwrite committed statements/line items (ever).
- Do not allow silent input drift after approval.
- Do not rely on floating point math; use decimal numerics.
- Do not hardcode jurisdiction rules into code paths; use rule versions.

---

## 9) Open choices to confirm early
- As-of date basis: pay_period_end vs check_date (pick one; recommended pay_period_end)
- Salary earnings: time/adjustment-driven vs synthetic periodic generation
- Preview persistence: stored vs ephemeral

If you need a decision, default to the recommendations in the specs.
