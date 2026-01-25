"""
Demo API - Read-Only Endpoints

This API exposes GET-only endpoints for the demo viewer.
All mutations are rejected at multiple layers:
1. No POST/PUT/PATCH/DELETE routes defined
2. Global middleware rejects non-GET methods
3. Database user has SELECT-only permissions
4. Connection uses default_transaction_read_only=on

Safety: This is a demo. No real money. No real providers.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
import databases
from pydantic import BaseModel


# ============================================================================
# Configuration
# ============================================================================

DATABASE_URL = os.environ.get(
    "DEMO_DATABASE_URL",
    "postgresql://demo_reader:demo@localhost:5432/payroll_demo"
)

# Force read-only at connection level
if "?" in DATABASE_URL:
    DATABASE_URL += "&options=-c%20default_transaction_read_only%3Don"
else:
    DATABASE_URL += "?options=-c%20default_transaction_read_only%3Don"

database = databases.Database(DATABASE_URL)


# ============================================================================
# Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    yield
    await database.disconnect()


# ============================================================================
# App Setup
# ============================================================================

app = FastAPI(
    title="Payroll Engine Demo API",
    description="Read-only API for the demo viewer. No mutations allowed.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS for demo UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Demo only - restrict in production
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================================
# Safety Middleware: Reject all non-GET methods
# ============================================================================

@app.middleware("http")
async def enforce_read_only(request: Request, call_next):
    if request.method not in ("GET", "OPTIONS", "HEAD"):
        return JSONResponse(
            status_code=405,
            content={
                "error": "Method not allowed",
                "detail": "This is a read-only demo API. Only GET requests are allowed.",
            },
        )
    return await call_next(request)


# ============================================================================
# Models
# ============================================================================

class HealthResponse(BaseModel):
    status: str
    database: str
    read_only: bool


class MetaResponse(BaseModel):
    version: str
    tenant_id: Optional[str]
    batch_id: Optional[str]
    seeded_at: Optional[str]


class EventResponse(BaseModel):
    id: str
    tenant_id: str
    event_type: str
    occurred_at: datetime
    correlation_id: Optional[str]
    payload: dict


class LedgerEntryResponse(BaseModel):
    id: str
    tenant_id: str
    entry_type: str
    debit_account_id: str
    credit_account_id: str
    amount: str
    memo: Optional[str]
    created_at: datetime
    source_type: Optional[str]
    source_id: Optional[str]


class AdvisoryResponse(BaseModel):
    advisory_id: str
    advisory_type: str
    confidence: float
    explanation: str
    payload: dict
    occurred_at: datetime


# ============================================================================
# Health & Metadata
# ============================================================================

@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
async def health():
    """Check API and database health."""
    try:
        result = await database.fetch_one("SELECT 1")
        db_status = "connected" if result else "error"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "read_only": True,
    }


@app.get("/api/meta", response_model=MetaResponse, tags=["Health"])
async def meta():
    """Get demo metadata (tenant ID, batch ID, seed time)."""
    meta_data = {}

    try:
        rows = await database.fetch_all("SELECT key, value FROM demo_meta")
        for row in rows:
            # value is JSONB, already parsed
            meta_data[row["key"]] = row["value"].strip('"') if isinstance(row["value"], str) else row["value"]
    except Exception:
        pass

    return {
        "version": "0.1.0",
        "tenant_id": meta_data.get("tenant_id"),
        "batch_id": meta_data.get("batch_id"),
        "seeded_at": meta_data.get("seeded_at"),
    }


# ============================================================================
# Events
# ============================================================================

@app.get("/api/events", tags=["Events"])
async def list_events(
    tenant_id: Optional[UUID] = None,
    event_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    after: Optional[datetime] = None,
    limit: int = Query(default=100, le=500),
):
    """List domain events with optional filters."""
    query = """
        SELECT id, tenant_id, event_type, occurred_at, correlation_id, payload
        FROM psp_domain_event
        WHERE 1=1
    """
    params = {}

    if tenant_id:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    if event_type:
        query += " AND event_type = :event_type"
        params["event_type"] = event_type

    if correlation_id:
        query += " AND correlation_id = :correlation_id"
        params["correlation_id"] = correlation_id

    if after:
        query += " AND occurred_at > :after"
        params["after"] = after

    query += " ORDER BY occurred_at DESC LIMIT :limit"
    params["limit"] = limit

    rows = await database.fetch_all(query, params)

    return [
        {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"].isoformat(),
            "correlation_id": row["correlation_id"],
            "payload": row["payload"],
        }
        for row in rows
    ]


@app.get("/api/events/{event_id}", tags=["Events"])
async def get_event(event_id: UUID):
    """Get a specific event by ID."""
    row = await database.fetch_one(
        """
        SELECT id, tenant_id, event_type, occurred_at, correlation_id, payload
        FROM psp_domain_event
        WHERE id = :event_id
        """,
        {"event_id": event_id},
    )

    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "event_type": row["event_type"],
        "occurred_at": row["occurred_at"].isoformat(),
        "correlation_id": row["correlation_id"],
        "payload": row["payload"],
    }


@app.get("/api/events/timeline/{correlation_id}", tags=["Events"])
async def get_timeline(correlation_id: str):
    """Get all events for a correlation ID (e.g., batch_id) as a timeline."""
    rows = await database.fetch_all(
        """
        SELECT id, tenant_id, event_type, occurred_at, correlation_id, payload
        FROM psp_domain_event
        WHERE correlation_id = :correlation_id
        ORDER BY occurred_at ASC
        """,
        {"correlation_id": correlation_id},
    )

    return {
        "correlation_id": correlation_id,
        "event_count": len(rows),
        "events": [
            {
                "id": str(row["id"]),
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"].isoformat(),
                "payload": row["payload"],
            }
            for row in rows
        ],
    }


# ============================================================================
# Ledger
# ============================================================================

@app.get("/api/ledger/entries", tags=["Ledger"])
async def list_ledger_entries(
    tenant_id: Optional[UUID] = None,
    entry_type: Optional[str] = None,
    account_id: Optional[UUID] = None,
    limit: int = Query(default=100, le=500),
):
    """List ledger entries with optional filters."""
    query = """
        SELECT id, tenant_id, entry_type, debit_account_id, credit_account_id,
               amount, memo, created_at, source_type, source_id
        FROM psp_ledger_entry
        WHERE 1=1
    """
    params = {}

    if tenant_id:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    if entry_type:
        query += " AND entry_type = :entry_type"
        params["entry_type"] = entry_type

    if account_id:
        query += " AND (debit_account_id = :account_id OR credit_account_id = :account_id)"
        params["account_id"] = account_id

    query += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit

    rows = await database.fetch_all(query, params)

    return [
        {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "entry_type": row["entry_type"],
            "debit_account_id": str(row["debit_account_id"]),
            "credit_account_id": str(row["credit_account_id"]),
            "amount": str(row["amount"]),
            "memo": row["memo"],
            "created_at": row["created_at"].isoformat(),
            "source_type": row["source_type"],
            "source_id": str(row["source_id"]) if row["source_id"] else None,
        }
        for row in rows
    ]


@app.get("/api/ledger/entries/{entry_id}", tags=["Ledger"])
async def get_ledger_entry(entry_id: UUID):
    """Get a specific ledger entry."""
    row = await database.fetch_one(
        """
        SELECT id, tenant_id, entry_type, debit_account_id, credit_account_id,
               amount, memo, created_at, source_type, source_id
        FROM psp_ledger_entry
        WHERE id = :entry_id
        """,
        {"entry_id": entry_id},
    )

    if not row:
        raise HTTPException(status_code=404, detail="Ledger entry not found")

    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "entry_type": row["entry_type"],
        "debit_account_id": str(row["debit_account_id"]),
        "credit_account_id": str(row["credit_account_id"]),
        "amount": str(row["amount"]),
        "memo": row["memo"],
        "created_at": row["created_at"].isoformat(),
        "source_type": row["source_type"],
        "source_id": str(row["source_id"]) if row["source_id"] else None,
    }


@app.get("/api/ledger/balances", tags=["Ledger"])
async def get_balances(tenant_id: Optional[UUID] = None):
    """Get account balances (computed from ledger entries)."""
    query = """
        WITH debits AS (
            SELECT debit_account_id AS account_id, SUM(amount) AS total
            FROM psp_ledger_entry
            WHERE 1=1
    """
    params = {}

    if tenant_id:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    query += """
            GROUP BY debit_account_id
        ),
        credits AS (
            SELECT credit_account_id AS account_id, SUM(amount) AS total
            FROM psp_ledger_entry
            WHERE 1=1
    """

    if tenant_id:
        query += " AND tenant_id = :tenant_id"

    query += """
            GROUP BY credit_account_id
        )
        SELECT
            COALESCE(d.account_id, c.account_id) AS account_id,
            COALESCE(d.total, 0) AS debits,
            COALESCE(c.total, 0) AS credits,
            COALESCE(d.total, 0) - COALESCE(c.total, 0) AS balance
        FROM debits d
        FULL OUTER JOIN credits c ON d.account_id = c.account_id
        ORDER BY balance DESC
    """

    rows = await database.fetch_all(query, params)

    return [
        {
            "account_id": str(row["account_id"]),
            "debits": str(row["debits"]),
            "credits": str(row["credits"]),
            "balance": str(row["balance"]),
        }
        for row in rows
    ]


# ============================================================================
# Advisories
# ============================================================================

@app.get("/api/advisories", tags=["Advisories"])
async def list_advisories(
    tenant_id: Optional[UUID] = None,
    advisory_type: Optional[str] = Query(None, description="return_analysis, funding_risk"),
    limit: int = Query(default=50, le=200),
):
    """List AI advisories from domain events."""
    query = """
        SELECT id, tenant_id, occurred_at, payload
        FROM psp_domain_event
        WHERE event_type = 'AIAdvisoryEmitted'
    """
    params = {}

    if tenant_id:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    if advisory_type:
        query += " AND payload->>'advisory_type' = :advisory_type"
        params["advisory_type"] = advisory_type

    query += " ORDER BY occurred_at DESC LIMIT :limit"
    params["limit"] = limit

    rows = await database.fetch_all(query, params)

    return [
        {
            "id": str(row["id"]),
            "advisory_id": row["payload"].get("advisory_id"),
            "advisory_type": row["payload"].get("advisory_type"),
            "confidence": row["payload"].get("confidence"),
            "explanation": row["payload"].get("explanation"),
            "occurred_at": row["occurred_at"].isoformat(),
            "payload": row["payload"],
        }
        for row in rows
    ]


@app.get("/api/advisories/{advisory_id}", tags=["Advisories"])
async def get_advisory(advisory_id: str):
    """Get a specific advisory by its advisory_id."""
    row = await database.fetch_one(
        """
        SELECT id, tenant_id, occurred_at, payload
        FROM psp_domain_event
        WHERE event_type = 'AIAdvisoryEmitted'
          AND payload->>'advisory_id' = :advisory_id
        """,
        {"advisory_id": advisory_id},
    )

    if not row:
        raise HTTPException(status_code=404, detail="Advisory not found")

    return {
        "id": str(row["id"]),
        "advisory_id": row["payload"].get("advisory_id"),
        "advisory_type": row["payload"].get("advisory_type"),
        "confidence": row["payload"].get("confidence"),
        "confidence_ceiling": row["payload"].get("confidence_ceiling"),
        "ambiguity_score": row["payload"].get("ambiguity_score"),
        "explanation": row["payload"].get("explanation"),
        "contributing_factors": row["payload"].get("contributing_factors", []),
        "recommended_action": row["payload"].get("recommended_action"),
        "model_version": row["payload"].get("model_version"),
        "occurred_at": row["occurred_at"].isoformat(),
    }


@app.get("/api/advisories/decisions", tags=["Advisories"])
async def list_advisory_decisions(tenant_id: Optional[UUID] = None):
    """List human decisions on AI advisories."""
    query = """
        SELECT id, tenant_id, advisory_id, advisory_type, decision,
               decided_by, decided_at, reason
        FROM psp_advisory_decision
        WHERE 1=1
    """
    params = {}

    if tenant_id:
        query += " AND tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    query += " ORDER BY decided_at DESC"

    rows = await database.fetch_all(query, params)

    return [
        {
            "id": str(row["id"]),
            "advisory_id": str(row["advisory_id"]),
            "advisory_type": row["advisory_type"],
            "decision": row["decision"],
            "decided_by": row["decided_by"],
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
            "reason": row["reason"],
        }
        for row in rows
    ]


# ============================================================================
# Reports (Computed from stored events - no DB writes)
# ============================================================================

@app.get("/api/reports/ai-advisory", tags=["Reports"])
async def get_ai_report(
    tenant_id: Optional[UUID] = None,
    since_days: int = Query(default=7, le=90),
    format: str = Query(default="json", regex="^(json|md)$"),
):
    """
    Generate AI advisory report from stored events.

    This computes the report in memory from domain events.
    No database writes occur.
    """
    # Get the pre-computed report event if available
    row = await database.fetch_one(
        """
        SELECT payload, occurred_at
        FROM psp_domain_event
        WHERE event_type = 'AIAdvisoryReportGenerated'
        ORDER BY occurred_at DESC
        LIMIT 1
        """,
    )

    if row:
        report = row["payload"]
        report["generated_at"] = row["occurred_at"].isoformat()
    else:
        # Compute from advisory events
        since = datetime.utcnow() - timedelta(days=since_days)
        advisories = await database.fetch_all(
            """
            SELECT payload FROM psp_domain_event
            WHERE event_type = 'AIAdvisoryEmitted'
              AND occurred_at > :since
            """,
            {"since": since},
        )

        report = {
            "period_days": since_days,
            "total_advisories": len(advisories),
            "advisories_by_type": {},
            "generated_at": datetime.utcnow().isoformat(),
        }

        for adv in advisories:
            adv_type = adv["payload"].get("advisory_type", "unknown")
            report["advisories_by_type"][adv_type] = (
                report["advisories_by_type"].get(adv_type, 0) + 1
            )

    if format == "md":
        md = f"""# AI Advisory Report

**Period**: Last {report.get('period_days', since_days)} days
**Generated**: {report.get('generated_at', 'N/A')}

## Summary

- Total advisories: {report.get('total_advisories', 0)}

## By Type

"""
        for adv_type, count in report.get("advisories_by_type", {}).items():
            md += f"- {adv_type}: {count}\n"

        if "accuracy_metrics" in report:
            md += f"""
## Accuracy

- Predictions made: {report['accuracy_metrics'].get('predictions_made', 0)}
- Accuracy rate: {report['accuracy_metrics'].get('accuracy_rate', 0):.1%}
"""

        return PlainTextResponse(md, media_type="text/markdown")

    return report


@app.get("/api/reports/tenant-risk", tags=["Reports"])
async def get_tenant_risk_report(
    tenant_id: Optional[UUID] = None,
    format: str = Query(default="json", regex="^(json|md)$"),
):
    """
    Get tenant risk profile from stored events.

    This returns the pre-computed profile. No database writes occur.
    """
    row = await database.fetch_one(
        """
        SELECT payload, occurred_at
        FROM psp_domain_event
        WHERE event_type = 'TenantRiskProfileGenerated'
        ORDER BY occurred_at DESC
        LIMIT 1
        """,
    )

    if not row:
        raise HTTPException(status_code=404, detail="No tenant risk profile found")

    profile = row["payload"]
    profile["generated_at"] = row["occurred_at"].isoformat()

    if format == "md":
        md = f"""# Tenant Risk Profile

**Tenant**: {profile.get('tenant_id', 'N/A')}
**Generated**: {profile.get('generated_at', 'N/A')}

## Risk Assessment

- **Overall Score**: {profile.get('overall_risk_score', 0):.2f}
- **Risk Tier**: {profile.get('risk_tier', 'N/A')}
- **Trend**: {profile.get('trend', 'N/A')}

## Metrics

"""
        for metric, value in profile.get("metrics", {}).items():
            md += f"- {metric}: {value}\n"

        if profile.get("recommended_checks"):
            md += "\n## Recommended Checks\n\n"
            for check in profile["recommended_checks"]:
                md += f"- {check}\n"

        return PlainTextResponse(md, media_type="text/markdown")

    return profile


@app.get("/api/reports/runbook", tags=["Reports"])
async def get_runbook_assistance(
    incident: str = Query(..., description="Incident type: payment_return, funding_block, etc."),
    return_code: Optional[str] = None,
    format: str = Query(default="json", regex="^(json|md)$"),
):
    """
    Get runbook assistance for an incident type.

    Returns pre-generated suggestions. No SQL is executed.
    SECURITY: Queries shown are suggestions only.
    """
    row = await database.fetch_one(
        """
        SELECT payload, occurred_at
        FROM psp_domain_event
        WHERE event_type = 'RunbookAssistanceGenerated'
        ORDER BY occurred_at DESC
        LIMIT 1
        """,
    )

    if not row:
        raise HTTPException(status_code=404, detail="No runbook assistance found")

    assistance = row["payload"]
    assistance["generated_at"] = row["occurred_at"].isoformat()

    if format == "md":
        md = f"""# Runbook Assistance

**Incident Type**: {assistance.get('incident_type', incident)}
**Return Code**: {assistance.get('return_code', return_code or 'N/A')}
**Generated**: {assistance.get('generated_at', 'N/A')}

## Checklist

"""
        for item in assistance.get("checklist", []):
            md += f"- [ ] {item}\n"

        md += "\n## Suggested Queries\n\n"
        md += "**SECURITY NOTE**: These are suggestions only. The system does NOT execute SQL.\n\n"

        for query in assistance.get("suggested_queries", []):
            md += f"### {query.get('name', 'Query')}\n\n"
            md += f"Purpose: {query.get('purpose', 'N/A')}\n\n"
            md += f"```sql\n{query.get('sql', '')}\n```\n\n"

        return PlainTextResponse(md, media_type="text/markdown")

    return assistance


# ============================================================================
# Root
# ============================================================================

@app.get("/", tags=["Health"])
async def root():
    """Demo API root."""
    return {
        "name": "Payroll Engine Demo API",
        "version": "0.1.0",
        "read_only": True,
        "docs": "/api/docs",
        "endpoints": {
            "health": "/api/health",
            "meta": "/api/meta",
            "events": "/api/events",
            "ledger": "/api/ledger/entries",
            "advisories": "/api/advisories",
            "reports": "/api/reports/ai-advisory",
        },
    }
