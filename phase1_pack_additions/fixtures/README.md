# Phase 1 Fixtures

These fixtures are intentionally minimal and deterministic.
They are designed to validate Phase 1 engine correctness, not UX.

Suggested load order:
1. tenant + legal_entity
2. jurisdiction + tax_agency
3. earning_code + deduction_code
4. employee + employment + pay_rate
5. pay_schedule + pay_period
6. time_entry
7. pay_run + pay_run_employee

Employees:
- EE1: Hourly, pre-tax 401k, state tax
- EE2: Hourly with shift diff + post-tax deduction

Expected:
- Deterministic gross/net
- Correct pre-tax wage reduction
- Correct tax line generation
- Net reconciliation without penny drift
