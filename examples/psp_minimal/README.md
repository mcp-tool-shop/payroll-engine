# PSP Minimal Example

See money move in 5 minutes.

## What This Demo Shows

1. **Create tenant + accounts** - Set up ledger accounts for a legal entity
2. **Fund the client account** - Simulate incoming wire transfer ($15,000)
3. **Commit payroll batch** - Evaluate funding gate, create reservation
4. **Execute payments** - 3 employees (ACH + FedNow) + 1 tax payment
5. **Simulate settlement feed** - Provider reports settlements + 1 return (R01)
6. **Classify liability** - Return creates liability event (employer responsible)
7. **Emit domain events** - Full event trail for audit/replay
8. **Show final state** - Balances, statuses, liability summary

## Quick Start

```bash
# From repo root:

# 1. Start Postgres
make up

# 2. Apply migrations
make migrate

# 3. Run the demo
make demo

# Or run directly:
cd examples/psp_minimal
python main.py --database-url "postgresql://user:pass@localhost/payroll_dev"
```

## Dry Run (No Database)

```bash
python main.py --dry-run
```

Output:
```
DRY RUN - No database operations
Would connect to: postgresql://localhost/payroll_dev
Tenant: abc123...
Employees: 3
  - Alice Johnson: $3,500.00 (ach)
  - Bob Smith: $4,200.00 (ach)
  - Carol Williams: $2,800.00 (fednow)
Tax: IRS Federal Tax: $2,100.00
Total payroll: $12,600.00
Return simulation: Bob Smith (R01)
```

## Expected Output

```
============================================================
  PSP MINIMAL EXAMPLE
============================================================
Tenant: 550e8400-e29b-41d4-a716-446655440000
Legal Entity: 6ba7b810-9dad-11d1-80b4-00c04fd430c8
Pay Period: 6ba7b811-9dad-11d1-80b4-00c04fd430c8

[Step 1] Create Ledger Accounts
----------------------------------------
  Account: client_funding_clearing -> abc...
  Account: psp_settlement_clearing -> def...
  ...

[Step 2] Fund Client Account
----------------------------------------
  Funded: $15,000.00 to client_funding_clearing
  Balance: $15,000.00

[Step 3] Commit Payroll (Funding Request + Reservation)
----------------------------------------
  Funding request: $12,600.00 (approved)
  Reserved: $12,600.00 for payroll

[Step 4] Create Payment Instructions
----------------------------------------
  Payment: Alice Johnson -> $3,500.00 (ach)
  Payment: Bob Smith -> $4,200.00 (ach)
  Payment: Carol Williams -> $2,800.00 (fednow)
  Payment: IRS Federal Tax -> $2,100.00 (ach)

[Step 5] Submit Payments to Providers
----------------------------------------
  Submitted: Alice Johnson -> ACHSTUB-a1b2c3d4e5f6
  Submitted: Bob Smith -> ACHSTUB-g7h8i9j0k1l2
  ...

[Step 7] Simulate Settlement Feed (with 1 return)
----------------------------------------
  Settled: Alice Johnson -> ACH123456789abc
  RETURNED: Bob Smith -> R01 (Insufficient Funds)
  Settled: Carol Williams -> ACH987654321def
  Settled: IRS Federal Tax -> ACHfederal12345

[Step 8] Classify Liability for Return
----------------------------------------
  Liability recorded: $4,200.00
    Origin: bank, Party: employer, Recovery: offset_future

============================================================
  FINAL SUMMARY
============================================================

Ledger Balances:
  client_funding_clearing: $6,600.00
  psp_settlement_clearing: $8,400.00
  ...

Payment Statuses:
  settled: 3 payments, $8,400.00
  returned: 1 payments, $4,200.00

Liability Events:
  $4,200.00: bank -> employer, offset_future (pending)

Domain Events: 14
Ledger Entries: 4

============================================================
  DEMO COMPLETE - Money moved!
============================================================
```

## Fixture Data

| Entity | Details |
|--------|---------|
| Alice Johnson | $3,500 net pay, ACH |
| Bob Smith | $4,200 net pay, ACH, **returns R01** |
| Carol Williams | $2,800 net pay, FedNow (instant) |
| IRS Federal Tax | $2,100 |
| **Total Payroll** | **$12,600** |
| Initial Funding | $15,000 |

## What the Return Demonstrates

Bob Smith's payment returns with code R01 (Insufficient Funds in recipient's account). This triggers:

1. **Settlement status**: `returned` instead of `settled`
2. **Liability classification**:
   - Error origin: `bank` (receiving bank rejected)
   - Liability party: `employer` (employer provided bad bank info)
   - Recovery path: `offset_future` (deduct from next payroll)
3. **No ledger debit** for returned payment (funds never left)
4. **Domain events**: `PaymentReturned` + `LiabilityClassified`

## Files

- `main.py` - Complete demo script
- `README.md` - This file

## Extending the Demo

### Add More Employees

Edit `EMPLOYEES` list in `main.py`:

```python
EMPLOYEES = [
    Employee(id=uuid4(), name="New Person", net_pay=Decimal("5000.00"),
             bank_account="****3456", rail="ach"),
    # ...
]
```

### Simulate Different Return Codes

Change `RETURN_EMPLOYEE` or modify `simulate_settlements()`:

```python
# In simulate_settlements():
return_code = "R02"  # Account Closed
return_reason = "Account Closed"
```

### Test Funding Gate Block

Set initial funding below payroll total:

```python
INITIAL_FUNDING = Decimal("5000.00")  # Less than $12,600 needed
```

## Troubleshooting

### "relation does not exist"

Migrations haven't been applied:
```bash
make migrate
```

### "connection refused"

PostgreSQL isn't running:
```bash
make up
```

### "permission denied"

Check DATABASE_URL credentials:
```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/payroll_dev"
```
