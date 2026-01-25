# 01 — Pay Run State Machine + Locking Contract (Phase 1)

**Objective:** Ensure payroll is **auditable**, **deterministic**, and safe from “silent drift” between preview, approval, commit, and pay.

## Entities involved
- `pay_run` (run container; status lives here)
- `pay_run_employee` (run membership + per-employee status/totals)
- `pay_statement` / `pay_line_item` (immutable outputs)
- Inputs/config that must freeze:
  - `time_entry`
  - `pay_input_adjustment`
  - effective-dated config: `employment`, `pay_rate`, `employee_deduction`, `employee_tax_profile`, `employee_payment_account`

## Status lifecycle
`draft → preview → approved → committed → paid` (+ `voided`)

### `draft`
**Intent:** Setup. Everything mutable.
- Allowed: edit run membership, inputs, and effective-dated config.
- Not allowed: committing immutable artifacts.

### `preview`
**Intent:** “What would payroll be right now?”
- Allowed: recalculation any time.
- Required: preview invalidation when inputs change.
- Not allowed: payment submission, immutable commit.

### `approved` (INPUTS FROZEN)
**Intent:** Human approval. From here, results must not change.
**Locking requirements:**
- Freeze in-scope `time_entry`, `pay_input_adjustment`
- Freeze effective-dated config *as of run as-of date*
- Disallow employee add/remove unless reopening.

**Implementation options for Phase 1:**
- (A) Snapshot IDs+hashes of input/config rows
- (B) Mark inputs as locked (recommended MVP) + reject updates
- (C) Full event sourcing (later)

### `committed` (RESULTS IMMUTABLE)
**Intent:** Financially real. Never overwrite results.
- Required: persist `pay_statement` + `pay_line_item` for all included employees
- Enforce immutability via app-layer + DB triggers/policies.

### `paid`
**Intent:** Funds disbursed/in flight.
- Allowed: GL export, reconciliation.
- Not allowed: modify committed artifacts.

### `voided`
**Intent:** Invalidated after commit.
- Never delete; create reversals and/or reissues.

## Allowed transitions
- `draft → preview`
- `preview → approved`
- `approved → preview` (reopen; log audit)
- `approved → committed`
- `committed → paid`
- `committed → voided` (admin + reason)
- `paid → voided` (rare; admin + reason)

## Lock timing summary
- **At approved:** lock inputs + effective-dated config used by the run.
- **At committed:** lock outputs forever.
