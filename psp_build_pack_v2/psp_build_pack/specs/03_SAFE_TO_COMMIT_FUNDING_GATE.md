# 03 — “Safe to Commit Payroll” Funding Gate (PSP Build)

**Goal:** Prevent committing a payroll run that the PSP cannot safely fund and disburse.

Payroll commit should be *financially safe*—not just mathematically correct.

## Definitions
- **Payroll Commit:** Creates immutable pay statements and liabilities (truth).
- **Funding Confirmation:** Confirms required funds are received/available to pay employees + obligations.
- **Funding Gate:** Policy + checks executed before allowing commit and/or before allowing payment submission.

## Recommended Two-Gate Model
### Gate A — Commit Gate (Policy-driven)
Decide whether payroll can be committed without confirmed funds.
Options:
1) **Strict PSP mode (recommended for PSP):** require funding confirmation before commit.
2) **Hybrid:** allow commit, but block payment submission until funded.
3) **Client-run mode:** allow commit with no funding (OSS/demo), but flag risk.

For a true PSP, pick **Strict** or **Hybrid**.

## Gate B — Pay Gate (Always required)
Regardless of commit policy, *do not submit payments* unless funds are available and reserved.

## Required Calculations for Funding Gate
For a pay_run:
- `total_net_pay` = sum(pay_statement.net_pay) for included employees
- `total_employee_taxes` = sum of employee tax line items (absolute value)
- `total_employer_taxes` = sum employer tax line items
- `total_third_party` = sum deductions earmarked to third parties (401k/HSA/garnishments)
- `psp_fees` (optional)
- `required_funding_amount` depends on funding model:
  - **prefund_all:** net + taxes + third-party + fees
  - **net_only:** net + third-party (if paid immediately)
  - **net_now_tax_later:** net + third-party now; taxes scheduled by due dates

## Funding Models (explicit)
- `prefund_all`
- `net_only`
- `net_and_third_party`
- `split_schedule` (net now, taxes later, third party later)

Each legal_entity must have a configured funding model.

## Gate Inputs (What to Check)
1) **Client funding status**
   - Has a funding request been created?
   - Has inbound settlement been *confirmed* (not merely submitted)?
   - Has any portion been returned/NSF?
2) **Ledger availability**
   - Available balance in `client_funding_clearing` ≥ required amount
3) **Reservations**
   - Create reservations for:
     - net pay
     - taxes (if funded now)
     - third-party (if funded now)
   - If reservation fails: gate fails
4) **Risk flags**
   - bank account changed within X hours
   - admin MFA disabled / role change
   - payroll delta spike vs prior periods
   - new client with no funding history
   - unusual number of manual checks/off-cycles

## Gate Outcomes
- PASS: allow commit and/or payment submission
- SOFT FAIL: allow commit but block payments (hybrid)
- HARD FAIL: block commit, provide actionable reasons

## Required Error Messages (Actionable)
- “Funding not received. Expected $X by {date}. Current available: $Y.”
- “NSF return received on funding debit. Payroll cannot be paid.”
- “High-risk bank change detected within cooldown window.”
- “Insufficient available funds to reserve taxes due on {due_date}.”

## Implementation Notes
- Use idempotency for gate evaluation: store a `funding_gate_evaluation` record per pay_run commit attempt.
- Use advisory lock on pay_run during gate evaluation + reservation creation.
- Never “partially reserve”: reservations must be all-or-nothing in a single DB transaction.

## Minimal Tables to Add
- `funding_request` (client → PSP pull)
- `funding_event` (settlement/return updates)
- `funding_gate_evaluation` (pass/fail + reasons json)
