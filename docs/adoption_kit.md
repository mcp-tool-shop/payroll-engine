# PSP Adoption Kit

This kit is the fastest path to evaluating, embedding, and operating the PSP library safely.

## 1) Install

### From source (recommended for evaluation)
```bash
git clone https://github.com/payroll-engine/payroll-engine.git
cd payroll-engine
pip install -e ".[dev]"
```

### Full local stack (recommended for running the demo)
```bash
pip install -e ".[all]"
```

**Notes:**
- This library is deterministic-by-default: no implicit env vars, globals, or hidden defaults.
- Database migrations must be applied by the adopter (forward-only).

**See also:**
- Public API contract: [docs/public_api.md](public_api.md)
- Non-goals: [docs/non_goals.md](non_goals.md)
- Compatibility policy: [docs/compat.md](compat.md)

---

## 2) Run the Demo (5–10 minutes)

### One-command demo (Postgres + migrations + lifecycle)
```bash
make up
make migrate
make demo
```

The demo runs an end-to-end lifecycle:
1. Commit payroll batch (commit gate + reservations)
2. Execute payments (pay gate + rail submission)
3. Simulate settlement + return
4. Reconcile settlements
5. Record liability
6. Replay domain events for deterministic debugging

**Entry point:**
- Demo code: [examples/psp_minimal/main.py](../examples/psp_minimal/main.py)

---

## 3) Embed the PSP Facade (the blessed integration path)

You should integrate via the facade, not internal services.

- **Facade**: `src/payroll_engine/psp/psp.py`
- **Config**: `src/payroll_engine/psp/config.py`

### Minimal shape
```python
from payroll_engine.psp import PSP, PSPConfig
from payroll_engine.psp.config import (
    LedgerConfig,
    FundingGateConfig,
    ProviderConfig,
    EventStoreConfig,
)

config = PSPConfig(
    tenant_id=tenant_id,
    legal_entity_id=legal_entity_id,
    ledger=LedgerConfig(require_balanced_entries=True),
    funding_gate=FundingGateConfig(
        commit_gate_enabled=True,
        pay_gate_enabled=True,  # NEVER False in production
    ),
    providers=[
        ProviderConfig(name="ach", rail="ach", credentials={...}),
    ],
    event_store=EventStoreConfig(),
)

psp = PSP(config=config, session=db_session)

# Commit payroll (creates reservation)
commit = psp.commit_payroll_batch(
    batch_id=batch_id,
    idempotency_key=f"commit:{batch_id}",
)

# Execute payments (pay gate runs automatically)
result = psp.execute_payments(
    batch_id=batch_id,
    idempotency_key=f"exec:{batch_id}",
)
```

### Hard rules

| Rule | Why |
|------|-----|
| Always pass stable `idempotency_key` for any operation that may be retried | Prevents double payments |
| Never write directly to ledger tables (use facade/services) | Enforces invariants |
| Never UPDATE/DELETE ledger or event store rows | Reversals and append-only are required |

### Verify DB is correctly hardened
```bash
psp schema-check --database-url "$DATABASE_URL"
```

---

## 4) Implement a Provider (bank-agnostic rails)

**Provider protocol:**
- `src/payroll_engine/psp/providers/base.py`

**Reference stubs:**
- `src/payroll_engine/psp/providers/ach_stub.py`
- `src/payroll_engine/psp/providers/fednow_stub.py`

### Checklist for a new provider

- [ ] Deterministic request IDs (or map provider IDs to internal IDs)
- [ ] Idempotent submit semantics
- [ ] Status callbacks can be replayed safely (duplicates, out-of-order)
- [ ] Settlement feed parsing yields stable trace IDs (uniqueness enforced)

**Tip:** Start by cloning the ACH stub, then replace the transport while keeping the same invariants.

**Recipe:** [docs/recipes/custom_provider.md](recipes/custom_provider.md)

---

## 5) Reconcile Settlements (facts drive truth)

Reconciliation is the bridge between "we submitted" and "money actually moved."

**Reconciliation services:**
- `src/payroll_engine/psp/services/reconciliation.py`

**Facade entry:**
- `PSP.ingest_settlement_feed(...)`

### Expected behavior

| Behavior | Why |
|----------|-----|
| Settlement events are immutable facts (append-only) | Audit trail |
| Matching is idempotent | Safe to reprocess |
| Status transitions implying money reversal create reversal entries | Ledger correctness |

### Operator flow
```bash
psp health --database-url "$DATABASE_URL"
psp balance --tenant-id <TENANT> --account-id <ACCOUNT>
psp replay-events --tenant-id <TENANT> --from-event-id <N>
```

**Runbook:** [docs/runbooks/settlement_mismatch.md](runbooks/settlement_mismatch.md)

---

## 6) Handle Returns + Liability (who pays, how recovery works)

Return handling is operational reality. The library tracks both:
- **Mechanical correction** (reversals / statuses)
- **Liability attribution** (error origin + party + recovery path)

**Liability service:**
- `src/payroll_engine/psp/services/liability.py`

**Return runbook:**
- [docs/runbooks/returns.md](runbooks/returns.md)

### What you should be able to answer from the system

| Question | Source |
|----------|--------|
| Which return codes are involved | `PaymentReturned` events |
| Which party is liable (employer / PSP / provider) | `LiabilityClassified` events |
| What recovery path is selected (offset / clawback / write-off) | `liability_event` table |
| Evidence trail | Domain events + ledger + liability records |

---

## 7) Upgrade Safely

Upgrades are forward-only and enforce event compatibility.

**Read:**
- Upgrade playbook: [docs/upgrading.md](upgrading.md)
- Compatibility rules: [docs/compat.md](compat.md)
- Idempotency guide: [docs/idempotency.md](idempotency.md)

### Before upgrading
```bash
psp export-events --database-url "$DATABASE_URL" --output events.jsonl
psp schema-check --database-url "$DATABASE_URL"
```

### After upgrading
```bash
psp schema-check --database-url "$DATABASE_URL"
psp health --database-url "$DATABASE_URL"
psp replay-events --database-url "$DATABASE_URL" --from-event-id <last_known_good>
```

---

## Reference

| Document | Purpose |
|----------|---------|
| [docs/invariants.md](invariants.md) | System invariants (what's guaranteed) |
| [docs/threat_model.md](threat_model.md) | Security analysis |
| [docs/public_api.md](public_api.md) | Public API contract |
| [docs/non_goals.md](non_goals.md) | What PSP doesn't do |
| [docs/runbooks/](runbooks/) | Operational procedures |
| [docs/recipes/](recipes/) | Integration examples |

**CLI reference:**
- `src/payroll_engine/psp/cli.py`
- Run `psp --help` for all commands
