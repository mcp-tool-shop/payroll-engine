# Payroll Engine

[![CI](https://github.com/payroll-engine/payroll-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/payroll-engine/payroll-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**A library-first PSP core for payroll and regulated money movement.**

Deterministic append-only ledger. Explicit funding gates. Replayable events. Advisory-only AI (disabled by default). Correctness over convenience.

## Quickstart

```bash
make up        # Start PostgreSQL
make migrate   # Apply migrations
make demo      # Run the demo
```

## Trust Anchors

Before adopting this library, review:

| Document | Purpose |
|----------|---------|
| [docs/psp_invariants.md](docs/psp_invariants.md) | System invariants (what's guaranteed) |
| [docs/threat_model.md](docs/threat_model.md) | Security analysis |
| [docs/public_api.md](docs/public_api.md) | Public API contract |
| [docs/compat.md](docs/compat.md) | Compatibility guarantees |
| [docs/adoption_kit.md](docs/adoption_kit.md) | Evaluation and embedding guide |

*We know this moves money. These documents prove we took it seriously.*

---

## Why This Exists

Most payroll systems treat money movement as an afterthought. They call a payment API, hope for the best, and deal with failures reactively. This creates:

- **Silent failures**: Payments vanish into the void
- **Reconciliation nightmares**: Bank statements don't match records
- **Liability confusion**: When returns happen, who pays?
- **Audit gaps**: No one can trace what actually happened

This project solves these problems by treating money movement as a first-class concern with proper financial engineering.

## Core Principles

### Why Append-Only Ledgers Matter

You can't undo a wire transfer. You can't un-send an ACH. The real world is append-only—so your ledger should be too.

```
❌ UPDATE ledger SET amount = 100 WHERE id = 1;  -- What was it before?
✅ INSERT INTO ledger (...) VALUES (...);         -- We reversed entry #1 for reason X
```

Every modification is a new entry. History is preserved. Auditors are happy.

### Why Two Funding Gates Exist

**Commit Gate**: "Do we have the money to promise these payments?"
**Pay Gate**: "Do we still have the money right before we send them?"

The time between commit and pay can be hours or days. Balances change. Other batches run. The pay gate is the final checkpoint—it runs even if someone tries to bypass it.

```python
# Commit time (Monday)
psp.commit_payroll_batch(batch)  # Reservation created

# Pay time (Wednesday)
psp.execute_payments(batch)      # Pay gate checks AGAIN before sending
```

### Why Settlement ≠ Payment

"Payment sent" is not "money moved." ACH takes 1-3 days. FedNow is instant but can still fail. Wire is same-day but expensive.

PSP tracks the full lifecycle:
```
Created → Submitted → Accepted → Settled (or Returned)
```

Until you see `Settled`, you don't have confirmation. Until you ingest the settlement feed, you don't know what really happened.

### Why Reversals Exist Instead of Deletes

When money moves wrong, you need a reversal—a new ledger entry that offsets the original. This:

- Preserves the audit trail (original + reversal)
- Shows *when* the correction happened
- Documents *why* (return code, reason)

```sql
-- Original
INSERT INTO ledger (amount, ...) VALUES (1000, ...);

-- Reversal (not delete!)
INSERT INTO ledger (amount, reversed_entry_id, ...) VALUES (-1000, <original_id>, ...);
```

### Why Idempotency is Mandatory

Network failures happen. Retries are necessary. Without idempotency, you get double payments.

Every operation in PSP has an idempotency key:
```python
result = psp.commit_payroll_batch(batch)
# First call: creates reservation, returns is_new=True
# Second call: finds existing, returns is_new=False, same reservation_id
```

The caller doesn't need to track "did my call succeed?"—just retry until you get a result.

## What This Is

A **reference-grade PSP core** suitable for:

- Payroll engines
- Gig economy platforms
- Benefits administrators
- Treasury management
- Any regulated fintech backend that moves money

## What This Is NOT

This is **not**:
- A Stripe clone (no merchant onboarding, no card processing)
- A payroll SaaS (no tax calculation, no UI)
- A demo or prototype (production-grade constraints)

See [docs/non_goals.md](docs/non_goals.md) for explicit non-goals.

## Quick Start

```bash
# Start PostgreSQL
make up

# Apply migrations
make migrate

# Run the demo
make demo
```

The demo shows the full lifecycle:
1. Create tenant and accounts
2. Fund the account
3. Commit a payroll batch (reservation)
4. Execute payments
5. Simulate settlement feed
6. Handle a return with liability classification
7. Replay events

## Library Usage

PSP is a library, not a service. Use it inside your application:

```python
from payroll_engine.psp import PSP, PSPConfig, LedgerConfig, FundingGateConfig

# Explicit configuration (no magic, no env vars)
config = PSPConfig(
    tenant_id=tenant_id,
    legal_entity_id=legal_entity_id,
    ledger=LedgerConfig(require_balanced_entries=True),
    funding_gate=FundingGateConfig(pay_gate_enabled=True),  # NEVER False
    providers=[...],
    event_store=EventStoreConfig(),
)

# Single entry point
psp = PSP(session=session, config=config)

# Commit payroll (creates reservation)
commit_result = psp.commit_payroll_batch(batch)

# Execute payments (pay gate runs automatically)
execute_result = psp.execute_payments(batch)

# Ingest settlement feed
ingest_result = psp.ingest_settlement_feed(records)
```

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/public_api.md](docs/public_api.md) | Public API contract (what's stable) |
| [docs/compat.md](docs/compat.md) | Versioning and compatibility |
| [docs/psp_invariants.md](docs/psp_invariants.md) | System invariants (what's guaranteed) |
| [docs/idempotency.md](docs/idempotency.md) | Idempotency patterns |
| [docs/threat_model.md](docs/threat_model.md) | Security analysis |
| [docs/non_goals.md](docs/non_goals.md) | What PSP doesn't do |
| [docs/upgrading.md](docs/upgrading.md) | Upgrade and migration guide |
| [docs/runbooks/](docs/runbooks/) | Operational procedures |
| [docs/recipes/](docs/recipes/) | Integration examples |

## Key Guarantees

| Guarantee | Enforcement |
|-----------|-------------|
| Money is always positive | `CHECK (amount > 0)` |
| No self-transfers | `CHECK (debit != credit)` |
| Ledger is append-only | No UPDATE/DELETE on entries |
| Status only moves forward | Trigger validates transitions |
| Events are immutable | Schema versioning in CI |
| Pay gate cannot be bypassed | Enforced in facade |

## CLI Tools

```bash
# Check database health
psp health

# Verify schema constraints
psp schema-check --database-url $DATABASE_URL

# Replay events
psp replay-events --tenant-id $TENANT --since "2025-01-01"

# Export events for audit
psp export-events --tenant-id $TENANT --output events.jsonl

# Query balance
psp balance --tenant-id $TENANT --account-id $ACCOUNT
```

## Installation

```bash
# Core only (ledger, funding gate, payments - that's it)
pip install payroll-engine

# With PostgreSQL driver
pip install payroll-engine[postgres]

# With async support
pip install payroll-engine[asyncpg]

# With AI advisory features (optional, disabled by default)
pip install payroll-engine[ai]

# Development
pip install payroll-engine[dev]

# Everything
pip install payroll-engine[all]
```

## Optional Dependencies

PSP is designed with strict optionality. **Core money movement requires zero optional dependencies.**

| Extra | What It Adds | Default State |
|-------|--------------|---------------|
| `[ai]` | ML-based AI models (future) | Not needed for rules-baseline |
| `[crypto]` | Blockchain integrations (future) | **OFF** - reserved for future |
| `[postgres]` | PostgreSQL driver | Not loaded unless used |
| `[asyncpg]` | Async PostgreSQL | Not loaded unless used |

### AI Advisory: Two-Tier System

**Rules-baseline AI works without any extras.** You get:
- Risk scoring
- Return analysis
- Runbook assistance
- Counterfactual simulation
- Tenant risk profiling

All with zero dependencies beyond stdlib.

```python
from payroll_engine.psp.ai import AdvisoryConfig, ReturnAdvisor

# Rules-baseline needs NO extras - just enable it
config = AdvisoryConfig(enabled=True, model_name="rules_baseline")
```

**ML models (future) require `[ai]` extras:**

```python
# Only needed for ML models, not rules-baseline
pip install payroll-engine[ai]

# Then use ML models
config = AdvisoryConfig(enabled=True, model_name="gradient_boost")
```

### AI Advisory Constraints (Enforced)

All AI features can **never**:
- Move money
- Write ledger entries
- Override funding gates
- Make settlement decisions

AI emits advisory events for human/policy review only.

See [docs/public_api.md](docs/public_api.md) for the full optionality table.

## Testing

```bash
# Unit tests
make test

# With database
make test-psp

# Red team tests (constraint verification)
pytest tests/psp/test_red_team_scenarios.py -v
```

## Who Should Use This

**Use PSP if you**:
- Move money in regulated contexts
- Need audit trails that satisfy compliance
- Care about correctness over convenience
- Have handled payment failures at 3 AM

**Don't use PSP if you**:
- Want a drop-in Stripe replacement
- Need a complete payroll solution
- Prefer convention over configuration

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Key rules:
- No new public API without updating `docs/public_api.md`
- Event schema changes must pass compatibility check
- All money operations require idempotency keys

## License

MIT License. See [LICENSE](LICENSE).

---

*Built by engineers who've been paged at 3 AM because payments failed silently.*
