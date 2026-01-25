# Recipe: Ledger + Reconciliation Only (No Funding Gate)

This recipe shows how to use PSP's ledger and reconciliation without the full funding gate. Useful when you already have external treasury controls.

## When to Use This

- You have an existing treasury system that controls fund releases
- You only need the ledger for tracking and reconciliation
- You're migrating incrementally and want to add funding gates later
- You're building internal tooling that tracks money but doesn't move it

## The Pattern

```python
"""
Ledger-Only PSP Usage

Use PSP for double-entry tracking and reconciliation without the funding gate.
Your external treasury system controls actual fund movement.
"""

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from payroll_engine.psp import PSP, PSPConfig
from payroll_engine.psp.config import (
    LedgerConfig,
    FundingGateConfig,
    EventStoreConfig,
)


def create_ledger_only_config(
    tenant_id: UUID,
    legal_entity_id: UUID,
) -> PSPConfig:
    """
    Create PSP config for ledger-only mode.

    WARNING: This disables funding gates. Only use if you have
    external treasury controls. Never use in production without
    understanding the implications.
    """
    return PSPConfig(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        ledger=LedgerConfig(
            require_balanced_entries=True,  # Always true - ledgers must balance
        ),
        funding_gate=FundingGateConfig(
            commit_gate_enabled=False,  # No reservation required
            pay_gate_enabled=False,     # No funding check on payment
        ),
        providers=[],  # No payment providers - ledger only
        event_store=EventStoreConfig(),
    )


# =============================================================================
# TRACKING EXTERNAL PAYMENTS
# =============================================================================

@dataclass
class ExternalPayment:
    """A payment made through your external treasury system."""
    external_ref: str
    amount: Decimal
    from_account: str
    to_account: str
    timestamp: datetime


def track_external_payment(psp: PSP, payment: ExternalPayment) -> UUID:
    """
    Record an external payment in PSP's ledger.

    This doesn't move money - it tracks what your treasury system did.
    """
    # Create ledger entry for the payment
    entry = psp.post_ledger_entry(
        debit_account_id=get_or_create_account(psp, payment.from_account),
        credit_account_id=get_or_create_account(psp, payment.to_account),
        amount=payment.amount,
        entry_type="external_payment",
        source_type="treasury",
        source_id=payment.external_ref,
        idempotency_key=f"ext:{payment.external_ref}",
        metadata={
            "external_ref": payment.external_ref,
            "timestamp": payment.timestamp.isoformat(),
        },
    )

    return entry.entry_id


def get_or_create_account(psp: PSP, account_ref: str) -> UUID:
    """Map external account reference to PSP account ID."""
    # Your implementation - might be a simple lookup table
    # or dynamic account creation
    ...


# =============================================================================
# RECONCILIATION WITH BANK FEEDS
# =============================================================================

@dataclass
class BankTransaction:
    """A transaction from your bank feed."""
    transaction_id: str
    amount: Decimal
    direction: str  # "credit" or "debit"
    counterparty: Optional[str]
    posted_date: datetime
    description: str


def reconcile_bank_feed(
    psp: PSP,
    transactions: list[BankTransaction],
    bank_account_id: UUID,
) -> dict:
    """
    Reconcile bank feed against PSP ledger.

    Returns reconciliation summary.
    """
    results = {
        "matched": [],
        "unmatched_bank": [],
        "unmatched_ledger": [],
    }

    # Get ledger entries for the period
    ledger_entries = psp.get_ledger_entries(
        account_id=bank_account_id,
        since=min(t.posted_date for t in transactions),
    )

    # Build lookup by amount + date (your matching logic may differ)
    ledger_by_key = {}
    for entry in ledger_entries:
        key = (entry.amount, entry.posted_at.date())
        ledger_by_key.setdefault(key, []).append(entry)

    # Match bank transactions to ledger entries
    for txn in transactions:
        key = (txn.amount, txn.posted_date.date())
        candidates = ledger_by_key.get(key, [])

        if candidates:
            # Found a match
            matched_entry = candidates.pop(0)  # FIFO matching
            results["matched"].append({
                "bank_txn": txn.transaction_id,
                "ledger_entry": matched_entry.entry_id,
                "amount": txn.amount,
            })

            # Mark as reconciled in PSP
            psp.mark_reconciled(
                entry_id=matched_entry.entry_id,
                bank_ref=txn.transaction_id,
            )
        else:
            # No match - bank has transaction we don't
            results["unmatched_bank"].append(txn)

    # Find ledger entries with no bank match
    for entries in ledger_by_key.values():
        for entry in entries:
            if not entry.reconciled:
                results["unmatched_ledger"].append(entry)

    return results


# =============================================================================
# QUERYING BALANCES AND POSITIONS
# =============================================================================

def get_position_report(psp: PSP, as_of: datetime) -> dict:
    """
    Get balance positions as of a specific time.

    Useful for:
    - End-of-day reporting
    - Audit snapshots
    - Treasury reconciliation
    """
    accounts = psp.list_accounts()

    positions = {}
    for account in accounts:
        balance = psp.get_balance(
            account_id=account.account_id,
            as_of=as_of,
        )
        positions[account.account_id] = {
            "account_type": account.account_type,
            "balance": balance,
            "as_of": as_of,
        }

    return {
        "positions": positions,
        "total_assets": sum(
            p["balance"] for p in positions.values()
            if p["account_type"] == "asset"
        ),
        "total_liabilities": sum(
            p["balance"] for p in positions.values()
            if p["account_type"] == "liability"
        ),
    }


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    # 1. Create ledger-only config
    config = create_ledger_only_config(
        tenant_id=uuid4(),
        legal_entity_id=uuid4(),
    )

    psp = PSP(config=config, session=your_db_session)

    # 2. Track payments made by external treasury
    external_payment = ExternalPayment(
        external_ref="TREAS-2025-001",
        amount=Decimal("50000.00"),
        from_account="operating",
        to_account="payroll_funding",
        timestamp=datetime.now(),
    )
    track_external_payment(psp, external_payment)

    # 3. Reconcile with bank feed
    bank_transactions = fetch_bank_feed()  # Your bank integration
    results = reconcile_bank_feed(
        psp=psp,
        transactions=bank_transactions,
        bank_account_id=bank_account_id,
    )

    print(f"Matched: {len(results['matched'])}")
    print(f"Unmatched bank: {len(results['unmatched_bank'])}")
    print(f"Unmatched ledger: {len(results['unmatched_ledger'])}")

    # 4. Generate position report
    report = get_position_report(psp, datetime.now())
    print(f"Total assets: {report['total_assets']}")
```

## Key Points

### Why Disable Funding Gates?

The funding gates exist to prevent unfunded payments. If you disable them:
- You can post ledger entries without reservations
- You can submit payments without balance checks
- **You are responsible for ensuring funds exist**

Only disable if you have external controls (treasury system, manual approval, etc.).

### Ledger Still Enforces Invariants

Even in ledger-only mode, PSP enforces:
- **Positive amounts** - No negative entries
- **No self-transfers** - Debit account ≠ credit account
- **Balanced books** - Every debit has a credit
- **Immutability** - Entries can't be edited, only reversed

### Reconciliation Patterns

Common matching strategies:
1. **Exact match** - Amount + date + reference
2. **Fuzzy match** - Amount + date within tolerance
3. **Many-to-one** - Multiple ledger entries match one bank txn
4. **Manual review** - Flag for human decision

PSP provides the primitives. Your matching logic depends on your bank and business rules.

### Migration Path to Full Funding

If you start ledger-only and want to add funding gates later:

1. Keep using ledger-only for historical data
2. Enable funding gates for new batches
3. Set a cutoff date
4. New payments go through full gates

```python
# Migration config
if payment_date >= FUNDING_GATE_CUTOFF:
    config = create_full_psp_config(...)
else:
    config = create_ledger_only_config(...)
```

## When NOT to Use This

Don't use ledger-only mode if:
- You're building net-new payment infrastructure
- You don't have external treasury controls
- You want PSP to prevent unfunded payments
- You're unsure - when in doubt, use full funding gates

The funding gates exist because "move money then check balance" is how payroll systems lose money.
