# OSS Contributing Guide — Payroll Engine + PSP Ops

This repository contains a production-grade payroll engine and PSP operations spine.

## Project goals
- Deterministic payroll calculations
- Immutable, auditable payroll ledger
- Idempotent commit and money movement primitives
- Pluggable rails (ACH/Wire/RTP/FedNow)
- PSP ledger (client funds, tax impound, third-party payables)
- Compliance-oriented change tracking (rule versions)

## Non-goals (for now)
- Global payroll
- HRIS / benefits enrollment UX
- UI-first features that bypass invariants

## How to contribute
1) Read `docs/ARCHITECTURE.md` and the specs in `specs/`
2) Pick a task labeled `good-first-issue` or `help-wanted`
3) Submit PRs that:
   - include tests
   - preserve determinism and immutability
   - include migration files for schema changes

## Golden Rules (PRs will be rejected if violated)
- Never overwrite committed payroll artifacts.
- Never mutate posted PSP ledger entries; use reversals.
- Never use floats for money; use Decimal/NUMERIC.
- Every externally triggered action must be idempotent.
- Every tax/compliance rule used must be versioned and traceable.

## Repo conventions
- `migrations/` numbered SQL migrations
- `specs/` authoritative behavior contracts
- `docs/` contributor-friendly guides
- `tests/` unit + integration coverage required

## Security
- No raw SSNs or bank account numbers in logs or fixtures.
- Tokenize sensitive identifiers.
- Security-related changes require an additional reviewer.
