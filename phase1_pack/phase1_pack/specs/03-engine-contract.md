# 03 — Engine Read/Write Contract (Phase 1)

## Invariants
- Deterministic outputs given same inputs + rule versions + engine version.
- Immutable results after commit.
- Explicit per-employee errors.

## As-of date standard
Recommended: `as_of_date = pay_period.period_end` for regular runs.
Off-cycle/manual: store explicit as_of_date on pay_run (add later if needed).

## Read set (per pay_run)
0) Load `pay_run`, validate status in (`draft`,`preview`,`approved`).
1) Load included employees from `pay_run_employee` (status != excluded).
2) Load effective `employment` for each employee+legal_entity as-of-date.
3) Load effective `employee_pay_schedule` (optional in phase 1 but recommended).
4) Load effective `pay_rate` rows.
5) Load inputs:
   - `time_entry` in pay period (and approved if enforced)
   - `pay_input_adjustment` targeted to run/period
6) Load effective `employee_deduction` + `garnishment_order` (priority ordered).
7) Load effective `employee_tax_profile` (state/local).
8) Resolve required `payroll_rule_version` IDs effective as-of-date.
   - Missing rule version => hard error.

## Calculation pipeline (stable order)
1) Earnings lines
2) Pre-tax deductions
3) Employee taxes (per jurisdiction)
4) Post-tax deductions
5) Garnishments
6) Employer taxes
7) Validate net and totals
8) Persist results

## Writes
- In preview/approved: update `pay_run_employee` gross/net/status; optionally persist preview statements.
- On commit: insert `pay_statement` + all `pay_line_item` rows, then set `pay_run.status=committed`.

## Lock expectations
At approved/commit, verify locked inputs/config have not changed since approval; otherwise abort.

## YTD contract
Compute YTD from prior committed line items (phase 1 acceptable), filtered by:
- employee_id
- tax year
- jurisdiction/tax basis
- exclude voided statements.

## Failure modes => `pay_run_employee.status=error`
- Missing employment, missing rate, missing rule version, invalid config overlap, negative net (unless allowed), integrity mismatch.
