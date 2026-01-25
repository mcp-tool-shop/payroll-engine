# Recipe: Embedding PSP into a Payroll Batch Run

This recipe shows how to embed PSP into a typical payroll engine batch run.

## Scenario

Your payroll engine calculates net pay for employees. You need to:
1. Reserve funds before processing
2. Create payment instructions
3. Submit to ACH/FedNow
4. Handle settlements and returns

## The Pattern

```python
"""
Payroll Batch Run with PSP Integration

This is the typical pattern for embedding PSP into a batch payroll run.
Copy and adapt to your payroll engine's structure.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator
from uuid import UUID, uuid4

from payroll_engine.psp import PSP, PSPConfig
from payroll_engine.psp.config import (
    LedgerConfig,
    FundingGateConfig,
    ProviderConfig,
    EventStoreConfig,
)


@dataclass
class PayrollItem:
    """Your payroll engine's output - adapt to your structure."""
    employee_id: UUID
    net_pay: Decimal
    bank_account: str
    routing_number: str


def run_payroll_batch(
    psp: PSP,
    payroll_items: list[PayrollItem],
    batch_id: UUID,
    funding_account_id: UUID,
) -> dict:
    """
    Execute a payroll batch through PSP.

    Returns summary of what happened.
    """
    results = {
        "batch_id": batch_id,
        "total_items": len(payroll_items),
        "funded": 0,
        "submitted": 0,
        "failed_funding": [],
        "failed_submit": [],
    }

    # =========================================================================
    # PHASE 1: Calculate total and check funding
    # =========================================================================
    total_amount = sum(item.net_pay for item in payroll_items)

    # Check if we have sufficient funds before doing anything
    available = psp.get_available_balance(funding_account_id)
    if available < total_amount:
        # Don't proceed with partial funding - that's a business decision
        raise InsufficientFundsError(
            f"Need {total_amount}, have {available}. "
            f"Shortfall: {total_amount - available}"
        )

    # =========================================================================
    # PHASE 2: Reserve funds (Commit Gate)
    # =========================================================================
    # This is the "commit gate" - funds are reserved but not yet spent
    reservation = psp.reserve_funds(
        account_id=funding_account_id,
        amount=total_amount,
        purpose=f"payroll_batch:{batch_id}",
        idempotency_key=f"reserve:{batch_id}",
    )

    if not reservation.success:
        raise FundingBlockedError(reservation.reason)

    results["reservation_id"] = reservation.reservation_id
    results["funded"] = len(payroll_items)

    # =========================================================================
    # PHASE 3: Create payment instructions
    # =========================================================================
    instructions = []
    for item in payroll_items:
        instruction = psp.create_payment_instruction(
            amount=item.net_pay,
            payee_account=item.bank_account,
            payee_routing=item.routing_number,
            payee_name=str(item.employee_id),  # Or actual name
            rail="ach",  # Or "fednow" for instant
            purpose="payroll",
            idempotency_key=f"pay:{batch_id}:{item.employee_id}",
            metadata={
                "batch_id": str(batch_id),
                "employee_id": str(item.employee_id),
            },
        )
        instructions.append(instruction)

    # =========================================================================
    # PHASE 4: Submit payments (Pay Gate)
    # =========================================================================
    # This is the "pay gate" - funds actually leave
    for instruction in instructions:
        result = psp.submit_payment(instruction.instruction_id)
        if result.success:
            results["submitted"] += 1
        else:
            results["failed_submit"].append({
                "instruction_id": instruction.instruction_id,
                "error": result.error_message,
            })

    # =========================================================================
    # PHASE 5: Return summary
    # =========================================================================
    # At this point:
    # - Funds are reserved
    # - Payments are submitted to the bank
    # - Settlements will come later (async, via webhook or bank feed)

    return results


class InsufficientFundsError(Exception):
    """Raised when funding account has insufficient balance."""
    pass


class FundingBlockedError(Exception):
    """Raised when funding is blocked by policy."""
    pass


# =============================================================================
# USAGE
# =============================================================================

if __name__ == "__main__":
    # 1. Configure PSP (do this once at app startup)
    config = PSPConfig(
        tenant_id=uuid4(),
        legal_entity_id=uuid4(),
        ledger=LedgerConfig(require_balanced_entries=True),
        funding_gate=FundingGateConfig(
            commit_gate_enabled=True,
            pay_gate_enabled=True,  # NEVER False in production
        ),
        providers=[
            ProviderConfig(
                name="your_ach_provider",
                rail="ach",
                credentials={"api_key": "..."},  # From secure config
            ),
        ],
        event_store=EventStoreConfig(),
    )

    # 2. Create PSP instance (inject your DB session)
    psp = PSP(config=config, session=your_db_session)

    # 3. Your payroll engine produces these
    payroll_items = [
        PayrollItem(
            employee_id=uuid4(),
            net_pay=Decimal("2500.00"),
            bank_account="123456789",
            routing_number="021000021",
        ),
        # ... more employees
    ]

    # 4. Run the batch
    try:
        results = run_payroll_batch(
            psp=psp,
            payroll_items=payroll_items,
            batch_id=uuid4(),
            funding_account_id=your_funding_account_id,
        )
        print(f"Batch complete: {results['submitted']}/{results['total_items']} submitted")
    except InsufficientFundsError as e:
        print(f"Cannot run payroll: {e}")
        # Alert treasury, pause batch, etc.
    except FundingBlockedError as e:
        print(f"Funding blocked: {e}")
        # Check policy, contact compliance, etc.
```

## Key Points

### The Two-Gate Model
1. **Commit Gate** (`reserve_funds`) - Funds are earmarked but not spent
2. **Pay Gate** (`submit_payment`) - Funds actually leave the account

You cannot skip the commit gate. If `commit_gate_enabled=False`, you can submit payments but they'll fail the pay gate check.

### Idempotency Keys
Every operation takes an idempotency key. Use a deterministic pattern:
- `reserve:{batch_id}` for reservations
- `pay:{batch_id}:{employee_id}` for payments

This means re-running a failed batch won't double-pay employees.

### Error Handling
- Check funding **before** creating instructions
- If funding fails, don't proceed with partial batch
- Track failed submissions separately for retry

### What Happens Next?
After `submit_payment`, you're waiting on the bank. PSP handles:
- **Settlement**: Bank confirms funds arrived (via webhook or bank feed)
- **Returns**: Bank returns the payment (NSF, invalid account, etc.)

See the [Settlement Recipe](./settlement_reconciliation.md) for handling bank responses.

## Variations

### Same-Day ACH vs Standard
```python
# Standard ACH (2-3 business days)
psp.create_payment_instruction(..., rail="ach", ...)

# Same-day ACH (faster, higher fees)
psp.create_payment_instruction(..., rail="ach_sameday", ...)

# FedNow (instant, when available)
psp.create_payment_instruction(..., rail="fednow", ...)
```

### Partial Batch Processing
If your business allows partial batches (some employees paid, others held):
```python
# Group by funding check
funded_items = []
unfunded_items = []

for item in payroll_items:
    if can_fund_individual(item):
        funded_items.append(item)
    else:
        unfunded_items.append(item)

# Process only funded items
run_payroll_batch(psp, funded_items, batch_id, funding_account_id)

# Handle unfunded separately
notify_treasury(unfunded_items)
```

### Multi-Entity Payroll
If you pay employees across multiple legal entities:
```python
# Group by legal entity
by_entity = group_by_legal_entity(payroll_items)

for entity_id, items in by_entity.items():
    # Each entity has its own PSP config and funding account
    entity_psp = get_psp_for_entity(entity_id)
    entity_funding = get_funding_account(entity_id)

    run_payroll_batch(entity_psp, items, batch_id, entity_funding)
```
