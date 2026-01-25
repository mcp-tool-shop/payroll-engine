# Payroll Engine Demo

A read-only demo viewer showing the complete payroll lifecycle with AI advisories.

## What It Shows

1. **Timeline** - Scrollable event timeline showing the full lifecycle:
   - Payroll batch committed
   - Funding gates evaluated
   - Payments submitted
   - Settlement received
   - Payment returned (R01)
   - Liability classified
   - AI advisories generated

2. **Ledger** - Append-only ledger entries:
   - Funding deposits
   - Reservations
   - Payments
   - Reversals (never deletes)

3. **Advisories** - AI advisory details:
   - Confidence scores and ceilings
   - Contributing factors with weights
   - Explanations
   - Model version tracking

4. **Reports** - Read-only reports:
   - AI Advisory Report
   - Tenant Risk Profile
   - Runbook Assistance

## Safety Model

**Defense in depth - read-only at every layer:**

| Layer | Protection |
|-------|------------|
| API | No POST/PUT/PATCH/DELETE routes |
| Middleware | Rejects all non-GET methods |
| DB User | SELECT-only permissions |
| DB Session | `default_transaction_read_only=on` |
| Providers | Stubs only, no network access |

## Quick Start (Local)

```bash
# 1. Start PostgreSQL
docker run -d --name demo-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=payroll_demo \
  -p 5432:5432 \
  postgres:16

# 2. Set up roles (optional but recommended)
psql -h localhost -U postgres -f demo/scripts/setup_db_roles.sql

# 3. Seed the database
DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/payroll_demo \
  python demo/scripts/seed.py --drop-first

# 4. Start the API
DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/payroll_demo \
  uvicorn demo.api.main:app --reload --port 8000

# 5. Open browser
open http://localhost:8000
```

## Deploy to Fly.io

```bash
# 1. Create app
fly apps create payroll-engine-demo

# 2. Create PostgreSQL
fly postgres create --name payroll-engine-demo-db

# 3. Attach database
fly postgres attach payroll-engine-demo-db --app payroll-engine-demo

# 4. Set database URL for demo reader
fly secrets set DEMO_DATABASE_URL="postgresql://..."

# 5. Deploy
fly deploy --config demo/fly.toml
```

## API Endpoints

All endpoints are GET-only.

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/meta` | Demo metadata (tenant ID, seed time) |
| `GET /api/events` | List domain events |
| `GET /api/events/{id}` | Get event details |
| `GET /api/events/timeline/{correlation_id}` | Get timeline for batch |
| `GET /api/ledger/entries` | List ledger entries |
| `GET /api/ledger/entries/{id}` | Get entry details |
| `GET /api/ledger/balances` | Get computed balances |
| `GET /api/advisories` | List AI advisories |
| `GET /api/advisories/{id}` | Get advisory details |
| `GET /api/advisories/decisions` | List human override decisions |
| `GET /api/reports/ai-advisory` | Generate AI report (from events) |
| `GET /api/reports/tenant-risk` | Get tenant risk profile |
| `GET /api/reports/runbook` | Get runbook assistance |

## Resetting Demo Data

```bash
# Local
./demo/scripts/reset.sh

# Fly.io
fly ssh console -C "python demo/scripts/seed.py --drop-first"
```

## Presentation Script (90 seconds)

1. **Open Timeline** - "Everything here is append-only facts. We can replay any incident."

2. **Click "PaymentReturned (R01)"** - Open event details, show return code and amount.

3. **Click "AIAdvisoryEmitted"** - Show confidence, contributing factors, explanation.

4. **Open Ledger** - "Notice the reversal entry. We never delete - only reverse."

5. **Open Reports → Tenant Risk** - "Trend flags and recommended checks."

That's it. Clean, professional, impressive.

## Files

```
demo/
├── api/
│   ├── __init__.py
│   └── main.py          # FastAPI read-only API
├── ui/
│   └── index.html       # Single-page demo viewer
├── scripts/
│   ├── seed.py          # Database seeder
│   ├── setup_db_roles.sql  # DB role setup
│   └── reset.sh         # Reset helper
├── Dockerfile           # Container build
├── fly.toml            # Fly.io config
└── README.md           # This file
```
