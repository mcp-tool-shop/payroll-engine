# Non-Goals

> This document defines what PSP is **not**. This protects the project from scope creep and sets clear expectations.

## What PSP Does NOT Provide

### We Do Not Provide UI

PSP is a library, not an application. There is no:

- Admin dashboard
- Employee portal
- Mobile app
- Reporting interface

**Why**: UI is an application concern. PSP provides the primitives; you build the interface.

### We Do Not Manage Bank Accounts

PSP does not:

- Open bank accounts
- Store bank credentials (beyond provider API keys)
- Manage banking relationships
- Handle bank onboarding

**Why**: This is a regulated activity requiring bank partnerships. PSP integrates with your existing banking relationships via payment providers.

### We Do Not Guarantee Tax Correctness

PSP handles money movement, not tax calculation. We do not:

- Calculate federal, state, or local taxes
- Determine tax jurisdiction
- File tax forms
- Provide tax advice

**Why**: Tax calculation is a separate domain with its own complexity (see [vertex.com](https://www.vertexinc.com/), [avalara.com](https://www.avalara.com/)). PSP moves the money once you've calculated the amounts.

### We Do Not Provide Fraud Scoring

PSP does not:

- Score transactions for fraud risk
- Block suspicious payments automatically
- Provide velocity checks
- Implement machine learning fraud detection

**Why**: Fraud detection requires domain expertise and data that PSP doesn't have. We provide hooks for you to integrate your own fraud scoring.

**What we do provide**:
- Event hooks where you can inject fraud checks
- The ability to reject payments in your pre-submission hooks
- Audit trail for forensic investigation

### We Do Not Abstract Accounting Policy Choices

PSP does not:

- Choose your chart of accounts
- Decide when to recognize revenue
- Determine cost center allocation
- Set depreciation schedules

**Why**: These are business policy decisions that vary by company, jurisdiction, and industry. PSP provides a ledger; you decide how to use it.

### We Do Not Handle Payroll Calculation

PSP does not:

- Calculate gross pay
- Compute overtime
- Determine benefits deductions
- Handle garnishments logic

**Why**: Payroll calculation is a massive domain. PSP handles the "pay" part of payroll, not the "roll" (calculation).

### We Do Not Provide Direct Employee Communication

PSP does not:

- Send pay stubs
- Email employees about deposits
- Provide SMS notifications
- Generate employee-facing documents

**Why**: Communication preferences and compliance (GDPR, etc.) vary. We emit events; you send communications.

### We Do Not Support Multi-Currency Natively

Current version handles USD only. We do not:

- Convert currencies
- Handle forex
- Manage multi-currency ledgers
- Support international payments

**Why**: Multi-currency adds complexity that's not needed for US payroll. Future versions may add this.

### We Do Not Provide Time & Attendance

PSP does not:

- Track hours worked
- Manage timesheets
- Handle PTO accrual
- Integrate with time clocks

**Why**: T&A is its own product category. PSP consumes the output (hours * rate = pay).

## What PSP IS

To be clear about what we **do** provide:

| Capability | PSP Provides |
|------------|-------------|
| **Ledger** | Append-only, balanced, auditable double-entry ledger |
| **Payment Rails** | ACH, FedNow, Wire submission and tracking |
| **Settlement** | Matching payments to bank statements |
| **Funding Control** | Two-gate model preventing unfunded payments |
| **Liability Attribution** | Classifying who's responsible for returns |
| **Event Sourcing** | Complete audit trail, replay capability |
| **Reconciliation** | Matching expected vs actual settlement |

## Why This Matters

Saying "no" is how projects stay focused and maintainable:

1. **Scope creep kills projects** - Every "just add X" compounds
2. **Expertise matters** - Tax, fraud, UI each require deep knowledge
3. **Integration beats monolith** - Better to compose specialized tools
4. **Maintenance burden** - Every feature needs tests, docs, support

## How to Handle Requests for Non-Goals

When someone asks for a non-goal feature:

1. **Point to this document** - "PSP doesn't handle X, see docs/non_goals.md"
2. **Explain the reasoning** - "Tax calculation is a separate domain..."
3. **Suggest integration** - "Emit events to your tax service"
4. **Close the issue** - "Won't implement, by design"

## Contributing New Non-Goals

If you identify something PSP should explicitly NOT do:

1. Open a PR adding it to this document
2. Explain why it's out of scope
3. Get maintainer approval

This document is part of the public API contract.
