# 02 — Line Item Semantics Spec (Phase 1)

## Goals
- Every paycheck = sum of immutable line items.
- Every line item is sourced, reproducible, auditable.

## Required fields (minimum)
- `pay_statement_id`
- `line_type` ∈ `EARNING | DEDUCTION | TAX | EMPLOYER_TAX | REIMBURSEMENT`
- `amount` (signed per conventions)
- Traceability:
  - `rule_id` (prefer always)
  - `rule_version_id` (required for TAX/EMPLOYER_TAX)
  - `source_input_id` (required for time/adjustment-based earnings)
  - `explanation`

## Sign conventions (non-negotiable)
- **EARNING:** positive
- **REIMBURSEMENT:** positive
- **DEDUCTION (employee):** negative
- **TAX (employee):** negative
- **EMPLOYER_TAX:** positive (employer liability)

**Net formula:**
`NET = Σ(EARNING) + Σ(REIMBURSEMENT) + Σ(DEDUCTION) + Σ(TAX)`

## Quantity/rate
- If hours*rate: store `quantity`, `rate`, and `amount`
- If flat: `quantity`/`rate` NULL

## Rounding
- USD to 2 decimals at persistence (line item creation).
- Internal compute at >=4 decimals.
- If penny drift exists, create explicit **Rounding adjustment** line item (do not smear).

## Taxability flags
Store `taxability_flags_json` on earnings/deductions with enough structure to:
- represent taxable wages basis per tax
- represent pre-tax deduction effects per tax

## Jurisdiction rules
- TAX/EMPLOYER_TAX: `jurisdiction_id` required; `rule_version_id` required.
- Other lines: optional `jurisdiction_id`.

## Employer-paid taxable benefits (imputed income)
If affects taxable wages but not cash:
- Add positive `EARNING` (taxable)
- Add offsetting `DEDUCTION` (non-cash offset) so cash/net stays correct

## Retro/corrections
- Retro represented as delta lines (no rewriting past).
- Voids/reissues: keep original, create reversal statement with negating lines.
