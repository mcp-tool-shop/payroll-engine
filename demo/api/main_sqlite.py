"""
Demo API - SQLite In-Memory Version

This version uses SQLite in-memory for zero-dependency local testing.
No PostgreSQL or Docker required.

Run with:
    cd F:/payroll-engine
    python -m uvicorn demo.api.main_sqlite:app --reload --port 8000

Then open: http://localhost:8000
"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4
import json
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from collections import defaultdict
import time
import html as html_escape


# ============================================================================
# In-Memory Database
# ============================================================================

DB_PATH = ":memory:"
_conn: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        init_schema(_conn)
        seed_demo_data(_conn)
    return _conn


def init_schema(conn: sqlite3.Connection):
    """Create tables in SQLite."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS demo_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS psp_domain_event (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            correlation_id TEXT,
            payload TEXT NOT NULL,
            schema_version INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS psp_ledger_entry (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            legal_entity_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            debit_account_id TEXT NOT NULL,
            credit_account_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            memo TEXT,
            created_at TEXT NOT NULL,
            source_type TEXT,
            source_id TEXT,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS psp_advisory_decision (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            advisory_id TEXT NOT NULL,
            advisory_type TEXT NOT NULL,
            decision TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_event_tenant ON psp_domain_event(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_event_type ON psp_domain_event(event_type);
        CREATE INDEX IF NOT EXISTS idx_event_correlation ON psp_domain_event(correlation_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_tenant ON psp_ledger_entry(tenant_id);

        -- Payroll tables
        CREATE TABLE IF NOT EXISTS earning_code (
            earning_code_id TEXT PRIMARY KEY,
            legal_entity_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            earning_category TEXT NOT NULL,
            is_taxable_federal INTEGER DEFAULT 1,
            is_taxable_state INTEGER DEFAULT 1,
            is_taxable_local INTEGER DEFAULT 1,
            gl_account_hint TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(legal_entity_id, code)
        );

        CREATE TABLE IF NOT EXISTS deduction_code (
            deduction_code_id TEXT PRIMARY KEY,
            legal_entity_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            deduction_type TEXT NOT NULL,
            calc_method TEXT NOT NULL,
            is_pretax INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(legal_entity_id, code)
        );

        CREATE TABLE IF NOT EXISTS employee (
            employee_id TEXT PRIMARY KEY,
            legal_entity_id TEXT NOT NULL,
            employee_number TEXT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            hire_date TEXT,
            pay_type TEXT DEFAULT 'hourly',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pay_schedule (
            pay_schedule_id TEXT PRIMARY KEY,
            legal_entity_id TEXT NOT NULL,
            name TEXT NOT NULL,
            frequency TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pay_period (
            pay_period_id TEXT PRIMARY KEY,
            pay_schedule_id TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            check_date TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pay_run (
            pay_run_id TEXT PRIMARY KEY,
            legal_entity_id TEXT NOT NULL,
            pay_period_id TEXT,
            run_type TEXT NOT NULL DEFAULT 'regular',
            status TEXT NOT NULL DEFAULT 'draft',
            committed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pay_statement (
            pay_statement_id TEXT PRIMARY KEY,
            pay_run_id TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            check_date TEXT NOT NULL,
            payment_method TEXT DEFAULT 'ach',
            gross_pay TEXT NOT NULL,
            net_pay TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pay_line_item (
            pay_line_item_id TEXT PRIMARY KEY,
            pay_statement_id TEXT NOT NULL,
            line_type TEXT NOT NULL,
            earning_code_id TEXT,
            deduction_code_id TEXT,
            description TEXT,
            hours TEXT,
            rate TEXT,
            amount TEXT NOT NULL,
            ytd_amount TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS time_entry (
            time_entry_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL,
            work_date TEXT NOT NULL,
            earning_code_id TEXT NOT NULL,
            hours TEXT,
            approved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_pay_statement_run ON pay_statement(pay_run_id);
        CREATE INDEX IF NOT EXISTS idx_pay_line_item_statement ON pay_line_item(pay_statement_id);
    """)
    conn.commit()


def seed_demo_data(conn: sqlite3.Connection):
    """Seed the demo scenario."""
    print("Seeding demo data...")

    tenant_id = str(uuid4())
    legal_entity_id = str(uuid4())
    batch_id = str(uuid4())
    reservation_id = str(uuid4())

    # Accounts
    payroll_funding_account = str(uuid4())
    employee_liability_account = str(uuid4())
    expense_account = str(uuid4())

    now = datetime.now(timezone.utc)
    commit_time = now - timedelta(days=3)
    pay_time = now - timedelta(days=2)
    settle_time = now - timedelta(days=1)
    return_time = now - timedelta(hours=6)

    # =========================================================================
    # Earning Codes
    # =========================================================================
    earning_codes = {
        "REG": {"id": str(uuid4()), "name": "Regular", "category": "regular", "taxable": True},
        "OT": {"id": str(uuid4()), "name": "Overtime", "category": "overtime", "taxable": True},
        "DT": {"id": str(uuid4()), "name": "Double Time", "category": "overtime", "taxable": True},
        "BONUS": {"id": str(uuid4()), "name": "Bonus", "category": "bonus", "taxable": True},
        "COMM": {"id": str(uuid4()), "name": "Commission", "category": "commission", "taxable": True},
        "PTO": {"id": str(uuid4()), "name": "Paid Time Off", "category": "pto", "taxable": True},
        "SICK": {"id": str(uuid4()), "name": "Sick Leave", "category": "sick", "taxable": True},
        "HOL": {"id": str(uuid4()), "name": "Holiday", "category": "holiday", "taxable": True},
        "REIMB": {"id": str(uuid4()), "name": "Expense Reimbursement", "category": "reimbursement", "taxable": False},
    }

    for code, data in earning_codes.items():
        conn.execute("""
            INSERT INTO earning_code (earning_code_id, legal_entity_id, code, name, earning_category,
                                      is_taxable_federal, is_taxable_state, is_taxable_local)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (data["id"], legal_entity_id, code, data["name"], data["category"],
              1 if data["taxable"] else 0, 1 if data["taxable"] else 0, 1 if data["taxable"] else 0))

    # =========================================================================
    # Deduction Codes
    # =========================================================================
    deduction_codes = {
        "401K": {"id": str(uuid4()), "name": "401(k) Traditional", "type": "pretax", "method": "percent"},
        "401K_R": {"id": str(uuid4()), "name": "401(k) Roth", "type": "roth", "method": "percent"},
        "HEALTH": {"id": str(uuid4()), "name": "Health Insurance", "type": "pretax", "method": "flat"},
        "DENTAL": {"id": str(uuid4()), "name": "Dental Insurance", "type": "pretax", "method": "flat"},
        "VISION": {"id": str(uuid4()), "name": "Vision Insurance", "type": "pretax", "method": "flat"},
        "HSA": {"id": str(uuid4()), "name": "HSA Contribution", "type": "pretax", "method": "flat"},
        "FSA": {"id": str(uuid4()), "name": "FSA Contribution", "type": "pretax", "method": "flat"},
        "LIFE": {"id": str(uuid4()), "name": "Life Insurance", "type": "posttax", "method": "flat"},
        "PARK": {"id": str(uuid4()), "name": "Parking", "type": "pretax", "method": "flat"},
    }

    for code, data in deduction_codes.items():
        conn.execute("""
            INSERT INTO deduction_code (deduction_code_id, legal_entity_id, code, name,
                                        deduction_type, calc_method, is_pretax)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data["id"], legal_entity_id, code, data["name"], data["type"], data["method"],
              1 if data["type"] == "pretax" else 0))

    # =========================================================================
    # Employees with detailed payroll info
    # =========================================================================
    employees = [
        {
            "id": str(uuid4()),
            "first_name": "John",
            "last_name": "Smith",
            "employee_number": "EMP-001",
            "hire_date": "2022-03-15",
            "pay_type": "salary",
            "hourly_rate": "45.67",  # ~$95k/yr
            "gross": "7307.69",
            "deductions": [
                {"code": "401K", "amount": "438.46", "ytd": "5261.52"},
                {"code": "HEALTH", "amount": "250.00", "ytd": "3000.00"},
                {"code": "DENTAL", "amount": "25.00", "ytd": "300.00"},
                {"code": "HSA", "amount": "17.31", "ytd": "207.72"},
            ],
            "taxes": [
                {"name": "Federal Income Tax", "amount": "657.69", "ytd": "7892.28"},
                {"name": "Social Security", "amount": "219.23", "ytd": "2630.76"},
                {"name": "Medicare", "amount": "51.23", "ytd": "614.76"},
                {"name": "CA State Tax", "amount": "146.15", "ytd": "1753.80"},
                {"name": "CA SDI", "amount": "21.85", "ytd": "262.20"},
            ],
            "earnings": [
                {"code": "REG", "hours": "80.00", "rate": "45.67", "amount": "3653.85"},
                {"code": "OT", "hours": "1.00", "rate": "68.51", "amount": "68.51"},
                {"code": "BONUS", "hours": None, "rate": None, "amount": "3585.33"},
            ],
        },
        {
            "id": str(uuid4()),
            "first_name": "Sarah",
            "last_name": "Johnson",
            "employee_number": "EMP-002",
            "hire_date": "2021-06-01",
            "pay_type": "salary",
            "hourly_rate": "50.48",  # ~$105k/yr
            "gross": "8076.92",
            "deductions": [
                {"code": "401K", "amount": "484.62", "ytd": "5815.44"},
                {"code": "HEALTH", "amount": "275.00", "ytd": "3300.00"},
                {"code": "VISION", "amount": "15.00", "ytd": "180.00"},
                {"code": "DENTAL", "amount": "25.00", "ytd": "300.00"},
                {"code": "LIFE", "amount": "8.07", "ytd": "96.84"},
            ],
            "taxes": [
                {"name": "Federal Income Tax", "amount": "727.92", "ytd": "8735.04"},
                {"name": "Social Security", "amount": "242.31", "ytd": "2907.72"},
                {"name": "Medicare", "amount": "56.54", "ytd": "678.48"},
                {"name": "CA State Tax", "amount": "161.54", "ytd": "1938.48"},
                {"name": "CA SDI", "amount": "23.23", "ytd": "278.76"},
            ],
            "earnings": [
                {"code": "REG", "hours": "80.00", "rate": "50.48", "amount": "4038.46"},
                {"code": "BONUS", "hours": None, "rate": None, "amount": "4038.46"},
            ],
        },
        {
            "id": str(uuid4()),
            "first_name": "Mike",
            "last_name": "Davis",
            "employee_number": "EMP-003",
            "hire_date": "2020-01-10",
            "pay_type": "hourly",
            "hourly_rate": "35.00",
            "gross": "3167.50",
            "deductions": [
                {"code": "401K", "amount": "190.05", "ytd": "2280.60"},
                {"code": "HEALTH", "amount": "95.03", "ytd": "1140.36"},
                {"code": "DENTAL", "amount": "25.00", "ytd": "300.00"},
                {"code": "VISION", "amount": "6.67", "ytd": "80.04"},
            ],
            "taxes": [
                {"name": "Federal Income Tax", "amount": "285.08", "ytd": "3420.96"},
                {"name": "Social Security", "amount": "95.03", "ytd": "1140.36"},
                {"name": "Medicare", "amount": "22.17", "ytd": "266.04"},
                {"name": "CA State Tax", "amount": "63.35", "ytd": "760.20"},
                {"name": "CA SDI", "amount": "9.50", "ytd": "114.00"},
            ],
            "earnings": [
                {"code": "REG", "hours": "80.00", "rate": "35.00", "amount": "2800.00"},
                {"code": "OT", "hours": "7.00", "rate": "52.50", "amount": "367.50"},
            ],
        },
    ]

    # Insert employees
    for emp in employees:
        conn.execute("""
            INSERT INTO employee (employee_id, legal_entity_id, employee_number, first_name,
                                  last_name, hire_date, pay_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (emp["id"], legal_entity_id, emp["employee_number"], emp["first_name"],
              emp["last_name"], emp["hire_date"], emp["pay_type"]))

    # =========================================================================
    # Pay Schedule & Period
    # =========================================================================
    pay_schedule_id = str(uuid4())
    pay_period_id = str(uuid4())
    pay_run_id = str(uuid4())

    conn.execute("""
        INSERT INTO pay_schedule (pay_schedule_id, legal_entity_id, name, frequency)
        VALUES (?, ?, 'Bi-Weekly', 'biweekly')
    """, (pay_schedule_id, legal_entity_id))

    period_start = (now - timedelta(days=17)).strftime("%Y-%m-%d")
    period_end = (now - timedelta(days=4)).strftime("%Y-%m-%d")
    check_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")

    conn.execute("""
        INSERT INTO pay_period (pay_period_id, pay_schedule_id, period_start, period_end, check_date, status)
        VALUES (?, ?, ?, ?, ?, 'paid')
    """, (pay_period_id, pay_schedule_id, period_start, period_end, check_date))

    conn.execute("""
        INSERT INTO pay_run (pay_run_id, legal_entity_id, pay_period_id, run_type, status, committed_at)
        VALUES (?, ?, ?, 'regular', 'committed', ?)
    """, (pay_run_id, legal_entity_id, pay_period_id, commit_time.isoformat()))

    # =========================================================================
    # Pay Statements & Line Items
    # =========================================================================
    for emp in employees:
        # Calculate net pay
        total_deductions = sum(Decimal(d["amount"]) for d in emp["deductions"])
        total_taxes = sum(Decimal(t["amount"]) for t in emp["taxes"])
        net_pay = Decimal(emp["gross"]) - total_deductions - total_taxes

        statement_id = str(uuid4())
        conn.execute("""
            INSERT INTO pay_statement (pay_statement_id, pay_run_id, employee_id, check_date,
                                       payment_method, gross_pay, net_pay)
            VALUES (?, ?, ?, ?, 'ach', ?, ?)
        """, (statement_id, pay_run_id, emp["id"], check_date, emp["gross"], str(net_pay)))

        emp["statement_id"] = statement_id
        emp["net_pay"] = str(net_pay)

        # Earning line items
        for earning in emp["earnings"]:
            ytd = str(Decimal(earning["amount"]) * 12)  # Approximate YTD
            conn.execute("""
                INSERT INTO pay_line_item (pay_line_item_id, pay_statement_id, line_type,
                                           earning_code_id, description, hours, rate, amount, ytd_amount)
                VALUES (?, ?, 'EARNING', ?, ?, ?, ?, ?, ?)
            """, (str(uuid4()), statement_id, earning_codes[earning["code"]]["id"],
                  earning_codes[earning["code"]]["name"], earning["hours"], earning["rate"],
                  earning["amount"], ytd))

        # Deduction line items
        for ded in emp["deductions"]:
            conn.execute("""
                INSERT INTO pay_line_item (pay_line_item_id, pay_statement_id, line_type,
                                           deduction_code_id, description, amount, ytd_amount)
                VALUES (?, ?, 'DEDUCTION', ?, ?, ?, ?)
            """, (str(uuid4()), statement_id, deduction_codes[ded["code"]]["id"],
                  deduction_codes[ded["code"]]["name"], ded["amount"], ded["ytd"]))

        # Tax line items
        for tax in emp["taxes"]:
            conn.execute("""
                INSERT INTO pay_line_item (pay_line_item_id, pay_statement_id, line_type,
                                           description, amount, ytd_amount)
                VALUES (?, ?, 'TAX', ?, ?, ?)
            """, (str(uuid4()), statement_id, tax["name"], tax["amount"], tax["ytd"]))

    # Store meta
    conn.execute("INSERT INTO demo_meta (key, value) VALUES (?, ?)",
                 ("tenant_id", tenant_id))
    conn.execute("INSERT INTO demo_meta (key, value) VALUES (?, ?)",
                 ("legal_entity_id", legal_entity_id))
    conn.execute("INSERT INTO demo_meta (key, value) VALUES (?, ?)",
                 ("batch_id", batch_id))
    conn.execute("INSERT INTO demo_meta (key, value) VALUES (?, ?)",
                 ("pay_run_id", pay_run_id))
    conn.execute("INSERT INTO demo_meta (key, value) VALUES (?, ?)",
                 ("seeded_at", now.isoformat()))

    # Events
    events = []

    # Batch committed
    events.append({
        "event_type": "PayrollBatchCommitted",
        "occurred_at": commit_time,
        "correlation_id": batch_id,
        "payload": {
            "batch_id": batch_id,
            "tenant_id": tenant_id,
            "employee_count": 3,
            "total_amount": "15000.00",
            "reservation_id": reservation_id,
        }
    })

    # Funding gates
    events.append({
        "event_type": "FundingGateEvaluated",
        "occurred_at": commit_time + timedelta(seconds=1),
        "correlation_id": batch_id,
        "payload": {
            "batch_id": batch_id,
            "gate_type": "commit",
            "result": "approved",
            "available_balance": "50000.00",
            "required_amount": "15000.00",
        }
    })

    events.append({
        "event_type": "FundingGateEvaluated",
        "occurred_at": pay_time,
        "correlation_id": batch_id,
        "payload": {
            "batch_id": batch_id,
            "gate_type": "pay",
            "result": "approved",
            "available_balance": "50000.00",
            "required_amount": "15000.00",
        }
    })

    # Payments submitted
    events.append({
        "event_type": "PaymentBatchSubmitted",
        "occurred_at": pay_time + timedelta(seconds=1),
        "correlation_id": batch_id,
        "payload": {
            "batch_id": batch_id,
            "provider": "ach_stub",
            "payment_count": 3,
            "total_amount": "15000.00",
        }
    })

    for emp in employees:
        payment_id = str(uuid4())
        emp["payment_id"] = payment_id
        emp_name = f"{emp['first_name']} {emp['last_name']}"
        events.append({
            "event_type": "PaymentSubmitted",
            "occurred_at": pay_time + timedelta(seconds=2),
            "correlation_id": batch_id,
            "payload": {
                "payment_id": payment_id,
                "batch_id": batch_id,
                "employee_name": emp_name,
                "amount": emp["net_pay"],
                "provider": "ach_stub",
            }
        })

    # Settlement
    events.append({
        "event_type": "SettlementFeedIngested",
        "occurred_at": settle_time,
        "correlation_id": batch_id,
        "payload": {
            "batch_id": batch_id,
            "settled_count": 2,
            "returned_count": 1,
            "settled_amount": "9500.00",
            "returned_amount": "5000.00",
        }
    })

    # Return for John Smith
    returned_employee = employees[0]
    returned_emp_name = f"{returned_employee['first_name']} {returned_employee['last_name']}"
    return_id = str(uuid4())
    events.append({
        "event_type": "PaymentReturned",
        "occurred_at": return_time,
        "correlation_id": batch_id,
        "payload": {
            "return_id": return_id,
            "payment_id": returned_employee["payment_id"],
            "employee_name": returned_emp_name,
            "amount": returned_employee["net_pay"],
            "return_code": "R01",
            "return_reason": "Insufficient Funds",
            "provider": "ach_stub",
        }
    })

    # Liability classified
    events.append({
        "event_type": "LiabilityClassified",
        "occurred_at": return_time + timedelta(seconds=1),
        "correlation_id": batch_id,
        "payload": {
            "return_id": return_id,
            "payment_id": returned_employee["payment_id"],
            "classification": "employee",
            "reason": "R01 - employee account issue",
            "amount": returned_employee["net_pay"],
        }
    })

    # AI Advisory - Return Analysis
    advisory_id = str(uuid4())
    events.append({
        "event_type": "AIAdvisoryEmitted",
        "occurred_at": return_time + timedelta(seconds=2),
        "correlation_id": batch_id,
        "payload": {
            "advisory_id": advisory_id,
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

    # AI Advisory - Funding Risk
    funding_advisory_id = str(uuid4())
    events.append({
        "event_type": "AIAdvisoryEmitted",
        "occurred_at": return_time + timedelta(seconds=3),
        "correlation_id": batch_id,
        "payload": {
            "advisory_id": funding_advisory_id,
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

    # Tenant Risk Profile
    events.append({
        "event_type": "TenantRiskProfileGenerated",
        "occurred_at": return_time + timedelta(seconds=4),
        "correlation_id": tenant_id,
        "payload": {
            "tenant_id": tenant_id,
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
                "Monitor R01 returns for John Smith",
                "Review funding buffer before next payroll",
            ],
        }
    })

    # Runbook Assistance
    events.append({
        "event_type": "RunbookAssistanceGenerated",
        "occurred_at": return_time + timedelta(seconds=5),
        "correlation_id": return_id,
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

    # AI Report
    events.append({
        "event_type": "AIAdvisoryReportGenerated",
        "occurred_at": now,
        "correlation_id": tenant_id,
        "payload": {
            "tenant_id": tenant_id,
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

    # Insert events
    for event in events:
        conn.execute("""
            INSERT INTO psp_domain_event (id, tenant_id, event_type, occurred_at, correlation_id, payload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(uuid4()),
            tenant_id,
            event["event_type"],
            event["occurred_at"].isoformat(),
            event.get("correlation_id"),
            json.dumps(event["payload"]),
        ))

    # Ledger entries
    ledger_entries = [
        {
            "entry_type": "funding",
            "debit": payroll_funding_account,
            "credit": expense_account,
            "amount": "50000.00",
            "memo": "Initial payroll funding",
            "created_at": commit_time - timedelta(days=7),
        },
    ]

    for emp in employees:
        emp_name = f"{emp['first_name']} {emp['last_name']}"
        ledger_entries.append({
            "entry_type": "reservation",
            "debit": employee_liability_account,
            "credit": payroll_funding_account,
            "amount": emp["net_pay"],
            "memo": f"Payroll reservation - {emp_name}",
            "created_at": commit_time,
        })
        ledger_entries.append({
            "entry_type": "payment",
            "debit": payroll_funding_account,
            "credit": employee_liability_account,
            "amount": emp["net_pay"],
            "memo": f"Payment disbursed - {emp_name}",
            "created_at": pay_time,
        })

    # Reversal for John Smith
    john_name = f"{employees[0]['first_name']} {employees[0]['last_name']}"
    ledger_entries.append({
        "entry_type": "reversal",
        "debit": employee_liability_account,
        "credit": payroll_funding_account,
        "amount": employees[0]["net_pay"],
        "memo": f"Payment reversal (R01) - {john_name}",
        "created_at": return_time,
    })

    for entry in ledger_entries:
        conn.execute("""
            INSERT INTO psp_ledger_entry (
                id, tenant_id, legal_entity_id, entry_type,
                debit_account_id, credit_account_id, amount,
                memo, created_at, source_type, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid4()),
            tenant_id,
            legal_entity_id,
            entry["entry_type"],
            entry["debit"],
            entry["credit"],
            entry["amount"],
            entry.get("memo"),
            entry["created_at"].isoformat(),
            "demo",
            str(uuid4()),
        ))

    # Advisory decision
    conn.execute("""
        INSERT INTO psp_advisory_decision (
            id, tenant_id, advisory_id, advisory_type, decision, decided_by, decided_at, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid4()),
        tenant_id,
        advisory_id,
        "return_analysis",
        "accepted",
        "system",
        (return_time + timedelta(minutes=5)).isoformat(),
        "Auto-accepted: high confidence recommendation",
    ))

    conn.commit()
    print(f"  Seeded {len(events)} events, {len(ledger_entries)} ledger entries")
    print(f"  Tenant ID: {tenant_id}")
    print(f"  Batch ID: {batch_id}")


# ============================================================================
# Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db()  # Initialize and seed
    yield


# ============================================================================
# App Setup
# ============================================================================

# Allowed origins for CORS (restrict in production)
ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",  # Dev server
    # Add your production domain here:
    # "https://demo.payroll-engine.com",
]

# Rate limiting configuration
RATE_LIMIT_REQUESTS = 60  # requests per window
RATE_LIMIT_WINDOW = 60  # seconds
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For for proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(client_ip: str) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Clean old entries
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if t > window_start
    ]

    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return False

    _rate_limit_store[client_ip].append(now)
    return True


app = FastAPI(
    title="Payroll Engine Demo API (SQLite)",
    description="Read-only demo with synthetic data. No PostgreSQL required.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS - restricted to allowed origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    allow_credentials=False,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Combined security middleware: read-only enforcement, rate limiting, security headers."""

    # 1. Enforce read-only (reject non-GET methods)
    if request.method not in ("GET", "OPTIONS", "HEAD"):
        return JSONResponse(
            status_code=405,
            content={"error": "Read-only demo. Only GET requests allowed."},
            headers=_security_headers(),
        )

    # 2. Rate limiting
    client_ip = get_client_ip(request)
    if not check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded. Try again later."},
            headers=_security_headers(),
        )

    # 3. Process request
    response = await call_next(request)

    # 4. Add security headers to all responses
    for header, value in _security_headers().items():
        response.headers[header] = value

    return response


def _security_headers() -> dict[str, str]:
    """Security headers for all responses."""
    return {
        # Prevent clickjacking
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "frame-ancestors 'none'",
        # Prevent MIME sniffing
        "X-Content-Type-Options": "nosniff",
        # XSS protection (legacy but still useful)
        "X-XSS-Protection": "1; mode=block",
        # Referrer policy
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Permissions policy (disable sensitive features)
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }


def sanitize_for_markdown(value: str) -> str:
    """
    Sanitize user-supplied values before interpolating into markdown.
    Escapes HTML entities and removes potential script injections.
    """
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    # Escape HTML entities
    sanitized = html_escape.escape(value)
    # Remove any remaining script-like patterns
    sanitized = sanitized.replace("javascript:", "")
    sanitized = sanitized.replace("data:", "")
    return sanitized


# ============================================================================
# Static UI
# ============================================================================

@app.get("/", include_in_schema=False)
async def serve_ui():
    ui_path = os.path.join(os.path.dirname(__file__), "..", "ui", "index.html")
    if os.path.exists(ui_path):
        return FileResponse(ui_path)
    return {"message": "Demo API", "docs": "/api/docs"}


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/api/health", tags=["Health"])
async def health():
    return {"status": "ok", "database": "sqlite_memory", "read_only": True}


@app.get("/api/meta", tags=["Health"])
async def meta():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM demo_meta").fetchall()
    meta = {row["key"]: row["value"] for row in rows}
    return {
        "version": "0.1.0",
        "database": "sqlite_memory",
        "tenant_id": meta.get("tenant_id"),
        "batch_id": meta.get("batch_id"),
        "seeded_at": meta.get("seeded_at"),
    }


@app.get("/api/events", tags=["Events"])
async def list_events(
    event_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
):
    conn = get_db()
    query = "SELECT * FROM psp_domain_event WHERE 1=1"
    params = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    if correlation_id:
        query += " AND correlation_id = ?"
        params.append(correlation_id)

    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"],
            "correlation_id": row["correlation_id"],
            "payload": json.loads(row["payload"]),
        }
        for row in rows
    ]


@app.get("/api/events/{event_id}", tags=["Events"])
async def get_event(event_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM psp_domain_event WHERE id = ?", (event_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"],
        "correlation_id": row["correlation_id"],
        "payload": json.loads(row["payload"]),
    }


@app.get("/api/ledger/entries", tags=["Ledger"])
async def list_ledger_entries(
    entry_type: Optional[str] = None,
    limit: int = Query(default=100, le=500),
):
    conn = get_db()
    query = "SELECT * FROM psp_ledger_entry WHERE 1=1"
    params = []

    if entry_type:
        query += " AND entry_type = ?"
        params.append(entry_type)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "entry_type": row["entry_type"],
            "debit_account_id": row["debit_account_id"],
            "credit_account_id": row["credit_account_id"],
            "amount": row["amount"],
            "memo": row["memo"],
            "created_at": row["created_at"],
            "source_type": row["source_type"],
        }
        for row in rows
    ]


# ============================================================================
# Payroll Endpoints
# ============================================================================

@app.get("/api/payroll/earning-codes", tags=["Payroll"])
async def list_earning_codes():
    """List all earning codes (REG, OT, BONUS, etc.)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM earning_code ORDER BY code
    """).fetchall()

    return [
        {
            "earning_code_id": row["earning_code_id"],
            "code": row["code"],
            "name": row["name"],
            "category": row["earning_category"],
            "is_taxable_federal": bool(row["is_taxable_federal"]),
            "is_taxable_state": bool(row["is_taxable_state"]),
            "is_taxable_local": bool(row["is_taxable_local"]),
        }
        for row in rows
    ]


@app.get("/api/payroll/deduction-codes", tags=["Payroll"])
async def list_deduction_codes():
    """List all deduction codes (401K, HEALTH, etc.)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM deduction_code ORDER BY code
    """).fetchall()

    return [
        {
            "deduction_code_id": row["deduction_code_id"],
            "code": row["code"],
            "name": row["name"],
            "deduction_type": row["deduction_type"],
            "calc_method": row["calc_method"],
            "is_pretax": bool(row["is_pretax"]),
        }
        for row in rows
    ]


@app.get("/api/payroll/employees", tags=["Payroll"])
async def list_employees():
    """List all employees with their pay statements."""
    conn = get_db()
    rows = conn.execute("""
        SELECT e.*, ps.pay_statement_id, ps.gross_pay, ps.net_pay, ps.check_date
        FROM employee e
        LEFT JOIN pay_statement ps ON e.employee_id = ps.employee_id
        ORDER BY e.last_name, e.first_name
    """).fetchall()

    return [
        {
            "employee_id": row["employee_id"],
            "employee_number": row["employee_number"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "hire_date": row["hire_date"],
            "pay_type": row["pay_type"],
            "status": row["status"],
            "latest_statement": {
                "pay_statement_id": row["pay_statement_id"],
                "gross_pay": row["gross_pay"],
                "net_pay": row["net_pay"],
                "check_date": row["check_date"],
            } if row["pay_statement_id"] else None,
        }
        for row in rows
    ]


@app.get("/api/payroll/pay-runs", tags=["Payroll"])
async def list_pay_runs():
    """List all pay runs."""
    conn = get_db()
    rows = conn.execute("""
        SELECT pr.*, pp.period_start, pp.period_end, pp.check_date,
               ps.name as schedule_name, ps.frequency
        FROM pay_run pr
        JOIN pay_period pp ON pr.pay_period_id = pp.pay_period_id
        JOIN pay_schedule ps ON pp.pay_schedule_id = ps.pay_schedule_id
        ORDER BY pp.check_date DESC
    """).fetchall()

    return [
        {
            "pay_run_id": row["pay_run_id"],
            "run_type": row["run_type"],
            "status": row["status"],
            "committed_at": row["committed_at"],
            "period": {
                "start": row["period_start"],
                "end": row["period_end"],
                "check_date": row["check_date"],
            },
            "schedule": {
                "name": row["schedule_name"],
                "frequency": row["frequency"],
            },
        }
        for row in rows
    ]


@app.get("/api/payroll/pay-statements", tags=["Payroll"])
async def list_pay_statements(
    employee_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """List pay statements with summary info."""
    conn = get_db()
    query = """
        SELECT ps.*, e.first_name, e.last_name, e.employee_number
        FROM pay_statement ps
        JOIN employee e ON ps.employee_id = e.employee_id
        WHERE 1=1
    """
    params = []

    if employee_id:
        query += " AND ps.employee_id = ?"
        params.append(employee_id)

    query += " ORDER BY ps.check_date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        {
            "pay_statement_id": row["pay_statement_id"],
            "employee": {
                "employee_id": row["employee_id"],
                "employee_number": row["employee_number"],
                "name": f"{row['first_name']} {row['last_name']}",
            },
            "check_date": row["check_date"],
            "payment_method": row["payment_method"],
            "gross_pay": row["gross_pay"],
            "net_pay": row["net_pay"],
        }
        for row in rows
    ]


@app.get("/api/payroll/pay-statements/{statement_id}", tags=["Payroll"])
async def get_pay_statement(statement_id: str):
    """Get a complete pay statement with all line items."""
    conn = get_db()

    # Get statement
    stmt = conn.execute("""
        SELECT ps.*, e.first_name, e.last_name, e.employee_number, e.pay_type
        FROM pay_statement ps
        JOIN employee e ON ps.employee_id = e.employee_id
        WHERE ps.pay_statement_id = ?
    """, (statement_id,)).fetchone()

    if not stmt:
        raise HTTPException(status_code=404, detail="Pay statement not found")

    # Get line items grouped by type
    line_items = conn.execute("""
        SELECT * FROM pay_line_item
        WHERE pay_statement_id = ?
        ORDER BY line_type, description
    """, (statement_id,)).fetchall()

    earnings = []
    deductions = []
    taxes = []

    for item in line_items:
        line = {
            "pay_line_item_id": item["pay_line_item_id"],
            "description": item["description"],
            "hours": item["hours"],
            "rate": item["rate"],
            "amount": item["amount"],
            "ytd_amount": item["ytd_amount"],
        }
        if item["line_type"] == "EARNING":
            earnings.append(line)
        elif item["line_type"] == "DEDUCTION":
            deductions.append(line)
        elif item["line_type"] == "TAX":
            taxes.append(line)

    return {
        "pay_statement_id": stmt["pay_statement_id"],
        "employee": {
            "employee_id": stmt["employee_id"],
            "employee_number": stmt["employee_number"],
            "name": f"{stmt['first_name']} {stmt['last_name']}",
            "pay_type": stmt["pay_type"],
        },
        "check_date": stmt["check_date"],
        "payment_method": stmt["payment_method"],
        "gross_pay": stmt["gross_pay"],
        "net_pay": stmt["net_pay"],
        "earnings": earnings,
        "deductions": deductions,
        "taxes": taxes,
        "totals": {
            "gross": stmt["gross_pay"],
            "deductions": str(sum(Decimal(d["amount"]) for d in deductions)),
            "taxes": str(sum(Decimal(t["amount"]) for t in taxes)),
            "net": stmt["net_pay"],
        },
    }


@app.get("/api/advisories", tags=["Advisories"])
async def list_advisories(
    advisory_type: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    conn = get_db()
    query = "SELECT * FROM psp_domain_event WHERE event_type = 'AIAdvisoryEmitted'"
    params = []

    if advisory_type:
        query += " AND json_extract(payload, '$.advisory_type') = ?"
        params.append(advisory_type)

    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        {
            "id": row["id"],
            "advisory_id": json.loads(row["payload"]).get("advisory_id"),
            "advisory_type": json.loads(row["payload"]).get("advisory_type"),
            "confidence": json.loads(row["payload"]).get("confidence"),
            "explanation": json.loads(row["payload"]).get("explanation"),
            "occurred_at": row["occurred_at"],
            "payload": json.loads(row["payload"]),
        }
        for row in rows
    ]


@app.get("/api/reports/ai-advisory", tags=["Reports"])
async def get_ai_report(format: str = Query(default="json", pattern="^(json|md)$")):
    conn = get_db()
    row = conn.execute("""
        SELECT payload, occurred_at FROM psp_domain_event
        WHERE event_type = 'AIAdvisoryReportGenerated'
        ORDER BY occurred_at DESC LIMIT 1
    """).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No report found")

    report = json.loads(row["payload"])
    report["generated_at"] = row["occurred_at"]

    if format == "md":
        md = f"""# AI Advisory Report

**Period**: Last {sanitize_for_markdown(str(report.get('period_days', 7)))} days
**Generated**: {sanitize_for_markdown(str(report.get('generated_at', 'N/A')))}

## Summary

- Total advisories: {report.get('total_advisories', 0)}

## By Type

"""
        for adv_type, count in report.get("advisories_by_type", {}).items():
            md += f"- {sanitize_for_markdown(str(adv_type))}: {count}\n"

        return PlainTextResponse(md, media_type="text/markdown")

    return report


@app.get("/api/reports/tenant-risk", tags=["Reports"])
async def get_tenant_risk(format: str = Query(default="json", pattern="^(json|md)$")):
    conn = get_db()
    row = conn.execute("""
        SELECT payload, occurred_at FROM psp_domain_event
        WHERE event_type = 'TenantRiskProfileGenerated'
        ORDER BY occurred_at DESC LIMIT 1
    """).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No profile found")

    profile = json.loads(row["payload"])
    profile["generated_at"] = row["occurred_at"]

    if format == "md":
        md = f"""# Tenant Risk Profile

**Risk Tier**: {sanitize_for_markdown(str(profile.get('risk_tier', 'N/A')))}
**Trend**: {sanitize_for_markdown(str(profile.get('trend', 'N/A')))}
**Score**: {profile.get('overall_risk_score', 0):.2f}

## Recommended Checks

"""
        for check in profile.get("recommended_checks", []):
            md += f"- {sanitize_for_markdown(str(check))}\n"

        return PlainTextResponse(md, media_type="text/markdown")

    return profile


@app.get("/api/reports/runbook", tags=["Reports"])
async def get_runbook(
    incident: str = Query(default="payment_return"),
    format: str = Query(default="json", pattern="^(json|md)$"),
):
    conn = get_db()
    row = conn.execute("""
        SELECT payload, occurred_at FROM psp_domain_event
        WHERE event_type = 'RunbookAssistanceGenerated'
        ORDER BY occurred_at DESC LIMIT 1
    """).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No runbook found")

    assistance = json.loads(row["payload"])
    assistance["generated_at"] = row["occurred_at"]

    if format == "md":
        md = f"""# Runbook Assistance

**Incident**: {sanitize_for_markdown(str(assistance.get('incident_type', incident)))}
**Return Code**: {sanitize_for_markdown(str(assistance.get('return_code', 'N/A')))}

## Checklist

"""
        for item in assistance.get("checklist", []):
            md += f"- [ ] {sanitize_for_markdown(str(item))}\n"

        md += "\n## Suggested Queries (READ-ONLY)\n\n"
        for q in assistance.get("suggested_queries", []):
            md += f"### {sanitize_for_markdown(str(q.get('name')))}\n```sql\n{sanitize_for_markdown(str(q.get('sql')))}\n```\n\n"

        return PlainTextResponse(md, media_type="text/markdown")

    return assistance


# ============================================================================
# Run
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 60)
    print("Payroll Engine Demo (SQLite In-Memory)")
    print("=" * 60)
    print("\nNo PostgreSQL required!")
    print("\nOpen: http://localhost:8000")
    print("API Docs: http://localhost:8000/api/docs")
    print("\n" + "=" * 60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
