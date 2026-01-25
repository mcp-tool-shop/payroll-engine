#!/usr/bin/env python3
"""
Demo Database Seeder

Seeds a PostgreSQL database with a complete demo scenario:
- 1 tenant, 3 employees, 1 tax agency, 1 third-party deduction
- Full payroll lifecycle: commit → pay → settle → return
- AI advisories and reports

Usage:
    python demo/scripts/seed.py --database-url postgresql://demo_writer:...@host/demo

Or with environment variable:
    DEMO_DATABASE_URL=postgresql://... python demo/scripts/seed.py
"""

import os
import sys
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import json

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def get_connection(database_url: str):
    """Get database connection."""
    try:
        import psycopg2
        return psycopg2.connect(database_url)
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)


def apply_migrations(conn, migrations_dir: str):
    """Apply all migrations in order."""
    import glob

    migration_files = sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))

    with conn.cursor() as cur:
        for migration_file in migration_files:
            print(f"  Applying: {os.path.basename(migration_file)}")
            with open(migration_file, 'r') as f:
                sql = f.read()
            try:
                cur.execute(sql)
            except Exception as e:
                # Skip if already applied (idempotent)
                if "already exists" in str(e).lower():
                    conn.rollback()
                    continue
                raise
    conn.commit()


def create_demo_scenario(conn):
    """Create the complete demo scenario."""

    # IDs for our demo entities
    tenant_id = uuid4()
    legal_entity_id = uuid4()

    # Accounts
    payroll_funding_account = uuid4()
    employee_liability_account = uuid4()
    tax_liability_account = uuid4()
    expense_account = uuid4()

    # Employees
    employees = [
        {"id": uuid4(), "name": "Alice Johnson", "amount": Decimal("5000.00")},
        {"id": uuid4(), "name": "Bob Smith", "amount": Decimal("4500.00")},
        {"id": uuid4(), "name": "Carol Williams", "amount": Decimal("5500.00")},
    ]

    # Payment batch
    batch_id = uuid4()
    reservation_id = uuid4()

    # Timestamps for the narrative
    now = datetime.now(timezone.utc)
    commit_time = now - timedelta(days=3)
    pay_time = now - timedelta(days=2)
    settle_time = now - timedelta(days=1)
    return_time = now - timedelta(hours=6)

    with conn.cursor() as cur:
        print("  Creating tenant and accounts...")

        # Create demo_meta table for tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS demo_meta (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Store demo IDs for API lookup
        cur.execute("""
            INSERT INTO demo_meta (key, value) VALUES
            ('tenant_id', %s),
            ('legal_entity_id', %s),
            ('batch_id', %s),
            ('seeded_at', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (
            json.dumps(str(tenant_id)),
            json.dumps(str(legal_entity_id)),
            json.dumps(str(batch_id)),
            json.dumps(now.isoformat()),
        ))

        # ===== DOMAIN EVENTS =====
        print("  Creating domain events timeline...")

        events = [
            # Batch committed
            {
                "event_type": "PayrollBatchCommitted",
                "occurred_at": commit_time,
                "correlation_id": str(batch_id),
                "payload": {
                    "batch_id": str(batch_id),
                    "tenant_id": str(tenant_id),
                    "employee_count": 3,
                    "total_amount": "15000.00",
                    "reservation_id": str(reservation_id),
                }
            },
            # Funding gate passed
            {
                "event_type": "FundingGateEvaluated",
                "occurred_at": commit_time + timedelta(seconds=1),
                "correlation_id": str(batch_id),
                "payload": {
                    "batch_id": str(batch_id),
                    "gate_type": "commit",
                    "result": "approved",
                    "available_balance": "50000.00",
                    "required_amount": "15000.00",
                }
            },
            # Pay gate passed
            {
                "event_type": "FundingGateEvaluated",
                "occurred_at": pay_time,
                "correlation_id": str(batch_id),
                "payload": {
                    "batch_id": str(batch_id),
                    "gate_type": "pay",
                    "result": "approved",
                    "available_balance": "50000.00",
                    "required_amount": "15000.00",
                }
            },
            # Payments submitted
            {
                "event_type": "PaymentBatchSubmitted",
                "occurred_at": pay_time + timedelta(seconds=1),
                "correlation_id": str(batch_id),
                "payload": {
                    "batch_id": str(batch_id),
                    "provider": "ach_stub",
                    "payment_count": 3,
                    "total_amount": "15000.00",
                }
            },
        ]

        # Add individual payment events
        for emp in employees:
            payment_id = uuid4()
            events.append({
                "event_type": "PaymentSubmitted",
                "occurred_at": pay_time + timedelta(seconds=2),
                "correlation_id": str(batch_id),
                "payload": {
                    "payment_id": str(payment_id),
                    "batch_id": str(batch_id),
                    "employee_name": emp["name"],
                    "amount": str(emp["amount"]),
                    "provider": "ach_stub",
                }
            })
            emp["payment_id"] = payment_id

        # Settlement received
        events.append({
            "event_type": "SettlementFeedIngested",
            "occurred_at": settle_time,
            "correlation_id": str(batch_id),
            "payload": {
                "batch_id": str(batch_id),
                "settled_count": 2,
                "returned_count": 1,
                "settled_amount": "9500.00",
                "returned_amount": "5000.00",
            }
        })

        # Return for Alice (R01 - Insufficient Funds)
        returned_employee = employees[0]
        return_id = uuid4()
        events.append({
            "event_type": "PaymentReturned",
            "occurred_at": return_time,
            "correlation_id": str(batch_id),
            "payload": {
                "return_id": str(return_id),
                "payment_id": str(returned_employee["payment_id"]),
                "employee_name": returned_employee["name"],
                "amount": str(returned_employee["amount"]),
                "return_code": "R01",
                "return_reason": "Insufficient Funds",
                "provider": "ach_stub",
            }
        })

        # Liability classified
        events.append({
            "event_type": "LiabilityClassified",
            "occurred_at": return_time + timedelta(seconds=1),
            "correlation_id": str(batch_id),
            "payload": {
                "return_id": str(return_id),
                "payment_id": str(returned_employee["payment_id"]),
                "classification": "employee",
                "reason": "R01 - employee account issue",
                "amount": str(returned_employee["amount"]),
            }
        })

        # AI Advisory generated
        advisory_id = uuid4()
        events.append({
            "event_type": "AIAdvisoryEmitted",
            "occurred_at": return_time + timedelta(seconds=2),
            "correlation_id": str(batch_id),
            "payload": {
                "advisory_id": str(advisory_id),
                "advisory_type": "return_analysis",
                "return_code": "R01",
                "confidence": 0.87,
                "confidence_ceiling": 0.92,
                "ambiguity_score": 0.15,
                "recommended_action": "contact_employee",
                "explanation": "R01 indicates insufficient funds in employee account. Historical data shows 73% of R01 returns for this employee segment are resolved within 3 days after employee notification.",
                "contributing_factors": [
                    {"factor": "return_code", "weight": 0.45, "value": "R01"},
                    {"factor": "employee_history", "weight": 0.25, "value": "first_return"},
                    {"factor": "amount_percentile", "weight": 0.15, "value": "p75"},
                    {"factor": "day_of_month", "weight": 0.10, "value": "end_of_month"},
                    {"factor": "employer_industry", "weight": 0.05, "value": "tech"},
                ],
                "model_version": "rules_baseline_v1",
                "feature_hash": "a1b2c3d4e5f6",
            }
        })

        # Funding risk advisory
        funding_advisory_id = uuid4()
        events.append({
            "event_type": "AIAdvisoryEmitted",
            "occurred_at": return_time + timedelta(seconds=3),
            "correlation_id": str(batch_id),
            "payload": {
                "advisory_id": str(funding_advisory_id),
                "advisory_type": "funding_risk",
                "risk_score": 0.23,
                "risk_level": "low",
                "confidence": 0.91,
                "explanation": "Funding risk is low. Current balance ($45,000) covers 3x the typical payroll amount. No concerning patterns detected.",
                "contributing_factors": [
                    {"factor": "balance_coverage_ratio", "weight": 0.40, "value": "3.0x"},
                    {"factor": "return_rate_30d", "weight": 0.30, "value": "0.033"},
                    {"factor": "balance_volatility", "weight": 0.20, "value": "low"},
                    {"factor": "days_to_next_payroll", "weight": 0.10, "value": "12"},
                ],
                "model_version": "rules_baseline_v1",
            }
        })

        # Tenant risk profile generated
        events.append({
            "event_type": "TenantRiskProfileGenerated",
            "occurred_at": return_time + timedelta(seconds=4),
            "correlation_id": str(tenant_id),
            "payload": {
                "tenant_id": str(tenant_id),
                "overall_risk_score": 0.28,
                "risk_tier": "standard",
                "trend": "stable",
                "metrics": {
                    "return_rate_30d": 0.033,
                    "avg_batch_size": 15000.00,
                    "funding_reliability": 0.95,
                    "payment_success_rate": 0.967,
                },
                "flags": [],
                "recommended_checks": [
                    "Monitor R01 returns for Alice Johnson",
                    "Review funding buffer before next payroll",
                ],
            }
        })

        # Runbook assistance generated
        events.append({
            "event_type": "RunbookAssistanceGenerated",
            "occurred_at": return_time + timedelta(seconds=5),
            "correlation_id": str(return_id),
            "payload": {
                "incident_type": "payment_return",
                "return_code": "R01",
                "suggested_queries": [
                    {
                        "name": "Find employee payment history",
                        "sql": "SELECT * FROM psp_payment_instruction WHERE employee_id = :employee_id ORDER BY created_at DESC LIMIT 10",
                        "purpose": "Review recent payments to this employee",
                    },
                    {
                        "name": "Check return patterns",
                        "sql": "SELECT return_code, COUNT(*) FROM psp_settlement_record WHERE status = 'returned' AND tenant_id = :tenant_id GROUP BY return_code",
                        "purpose": "Identify most common return reasons",
                    },
                ],
                "checklist": [
                    "Verify employee bank account details are correct",
                    "Contact employee about account balance",
                    "Schedule retry payment for next business day",
                    "Update employee record if account changed",
                ],
                "note": "SECURITY: These queries are suggestions only. The system does NOT execute SQL.",
            }
        })

        # AI Report generated
        events.append({
            "event_type": "AIAdvisoryReportGenerated",
            "occurred_at": now,
            "correlation_id": str(tenant_id),
            "payload": {
                "tenant_id": str(tenant_id),
                "period_days": 7,
                "total_advisories": 2,
                "advisories_by_type": {
                    "return_analysis": 1,
                    "funding_risk": 1,
                },
                "accuracy_metrics": {
                    "predictions_made": 2,
                    "outcomes_known": 1,
                    "correct_predictions": 1,
                    "accuracy_rate": 1.0,
                },
                "human_overrides": {
                    "total_overrides": 0,
                    "override_rate": 0.0,
                },
                "confidence_distribution": {
                    "high_confidence": 2,
                    "medium_confidence": 0,
                    "low_confidence": 0,
                },
            }
        })

        # Insert all events
        for event in events:
            cur.execute("""
                INSERT INTO psp_domain_event (
                    id, tenant_id, event_type, occurred_at, correlation_id, payload, schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 1
                )
            """, (
                uuid4(),
                tenant_id,
                event["event_type"],
                event["occurred_at"],
                event.get("correlation_id"),
                json.dumps(event["payload"]),
            ))

        print(f"  Created {len(events)} domain events")

        # ===== LEDGER ENTRIES =====
        print("  Creating ledger entries...")

        ledger_entries = []

        # Initial funding (deposit to payroll account)
        ledger_entries.append({
            "entry_type": "funding",
            "debit_account": payroll_funding_account,
            "credit_account": expense_account,
            "amount": Decimal("50000.00"),
            "memo": "Initial payroll funding",
            "created_at": commit_time - timedelta(days=7),
        })

        # Reservation entries (commit gate)
        for emp in employees:
            entry_id = uuid4()
            ledger_entries.append({
                "id": entry_id,
                "entry_type": "reservation",
                "debit_account": employee_liability_account,
                "credit_account": payroll_funding_account,
                "amount": emp["amount"],
                "memo": f"Payroll reservation - {emp['name']}",
                "created_at": commit_time,
                "source_id": batch_id,
            })
            emp["reservation_entry_id"] = entry_id

        # Payment entries (pay gate)
        for emp in employees:
            ledger_entries.append({
                "entry_type": "payment",
                "debit_account": payroll_funding_account,
                "credit_account": employee_liability_account,
                "amount": emp["amount"],
                "memo": f"Payment disbursed - {emp['name']}",
                "created_at": pay_time,
                "source_id": emp["payment_id"],
            })

        # Reversal entry for returned payment (Alice)
        returned_emp = employees[0]
        reversal_entry_id = uuid4()
        ledger_entries.append({
            "id": reversal_entry_id,
            "entry_type": "reversal",
            "debit_account": employee_liability_account,
            "credit_account": payroll_funding_account,
            "amount": returned_emp["amount"],
            "memo": f"Payment reversal (R01) - {returned_emp['name']}",
            "created_at": return_time,
            "source_id": return_id,
            "reversed_entry_id": returned_emp.get("reservation_entry_id"),
        })

        # Insert ledger entries
        for entry in ledger_entries:
            cur.execute("""
                INSERT INTO psp_ledger_entry (
                    id, tenant_id, legal_entity_id, entry_type,
                    debit_account_id, credit_account_id, amount,
                    memo, created_at, source_type, source_id, idempotency_key
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                entry.get("id", uuid4()),
                tenant_id,
                legal_entity_id,
                entry["entry_type"],
                entry["debit_account"],
                entry["credit_account"],
                entry["amount"],
                entry.get("memo"),
                entry.get("created_at", now),
                "demo",
                entry.get("source_id", uuid4()),
                str(uuid4()),  # idempotency_key
            ))

        print(f"  Created {len(ledger_entries)} ledger entries")

        # ===== ADVISORY DECISIONS =====
        print("  Creating advisory decision records...")

        cur.execute("""
            INSERT INTO psp_advisory_decision (
                id, tenant_id, advisory_id, advisory_type, decision,
                decided_by, decided_at, reason, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """, (
            uuid4(),
            tenant_id,
            advisory_id,
            "return_analysis",
            "accepted",
            "system",
            return_time + timedelta(minutes=5),
            "Auto-accepted: high confidence recommendation",
            return_time + timedelta(minutes=5),
        ))

        print("  Created advisory decision records")

    conn.commit()
    print(f"\n  Demo tenant ID: {tenant_id}")
    print(f"  Demo batch ID: {batch_id}")


def main():
    parser = argparse.ArgumentParser(description="Seed demo database")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DEMO_DATABASE_URL"),
        help="PostgreSQL connection URL (or set DEMO_DATABASE_URL)",
    )
    parser.add_argument(
        "--migrations-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "..", "migrations"),
        help="Path to migrations directory",
    )
    parser.add_argument(
        "--drop-first",
        action="store_true",
        help="Drop and recreate all tables before seeding",
    )

    args = parser.parse_args()

    if not args.database_url:
        print("ERROR: --database-url required (or set DEMO_DATABASE_URL)")
        sys.exit(1)

    print("=" * 60)
    print("Demo Database Seeder")
    print("=" * 60)

    conn = get_connection(args.database_url)

    try:
        if args.drop_first:
            print("\n[1/3] Dropping existing tables...")
            with conn.cursor() as cur:
                cur.execute("""
                    DROP TABLE IF EXISTS demo_meta CASCADE;
                    DROP TABLE IF EXISTS psp_advisory_decision CASCADE;
                    DROP TABLE IF EXISTS psp_domain_event CASCADE;
                    DROP TABLE IF EXISTS psp_ledger_entry CASCADE;
                    DROP TABLE IF EXISTS psp_funding_request CASCADE;
                    DROP TABLE IF EXISTS psp_payment_instruction CASCADE;
                    DROP TABLE IF EXISTS psp_settlement_record CASCADE;
                    DROP TABLE IF EXISTS psp_liability_record CASCADE;
                """)
            conn.commit()
            print("  Done")
        else:
            print("\n[1/3] Skipping drop (use --drop-first to reset)")

        print("\n[2/3] Applying migrations...")
        apply_migrations(conn, args.migrations_dir)
        print("  Done")

        print("\n[3/3] Creating demo scenario...")
        create_demo_scenario(conn)
        print("  Done")

        print("\n" + "=" * 60)
        print("Demo database seeded successfully!")
        print("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
