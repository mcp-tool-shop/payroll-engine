"""API endpoint integration tests.

Tests the FastAPI endpoints for pay run operations.
"""

import pytest
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    DEMO_TENANT_ID,
    DEMO_LEGAL_ENTITY_ID,
    DEMO_PAY_SCHEDULE_ID,
    DRAFT_PAY_RUN_ID,
)


pytestmark = pytest.mark.asyncio


class TestHealthEndpoints:
    """Test health check endpoints."""

    async def test_health_check(self, client: AsyncClient):
        """Health endpoint should return 200."""
        response = await client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "timestamp" in data

    async def test_readiness_check(self, client: AsyncClient):
        """Readiness endpoint should return 200."""
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    async def test_liveness_check(self, client: AsyncClient):
        """Liveness endpoint should return 200."""
        response = await client.get("/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"


class TestPayRunCRUD:
    """Test pay run CRUD endpoints."""

    async def test_create_pay_run(self, client: AsyncClient, seeded_db: AsyncSession):
        """POST /api/v1/pay-runs should create a new pay run."""
        response = await client.post(
            "/api/v1/pay-runs",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={
                "legal_entity_id": str(DEMO_LEGAL_ENTITY_ID),
                "pay_schedule_id": str(DEMO_PAY_SCHEDULE_ID),
                "period_start": "2026-01-19",
                "period_end": "2026-02-01",
                "check_date": "2026-02-05",
                "run_type": "regular",
            },
        )

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "draft"
        assert data["tenant_id"] == str(DEMO_TENANT_ID)
        assert data["legal_entity_id"] == str(DEMO_LEGAL_ENTITY_ID)

    async def test_create_pay_run_requires_tenant_id(self, client: AsyncClient):
        """Creating pay run without X-Tenant-ID should fail."""
        response = await client.post(
            "/api/v1/pay-runs",
            json={
                "legal_entity_id": str(DEMO_LEGAL_ENTITY_ID),
                "pay_schedule_id": str(DEMO_PAY_SCHEDULE_ID),
                "period_start": "2026-01-19",
                "period_end": "2026-02-01",
                "check_date": "2026-02-05",
            },
        )

        assert response.status_code == 400
        assert "X-Tenant-ID" in response.json()["detail"]

    async def test_list_pay_runs(self, client: AsyncClient, seeded_db: AsyncSession):
        """GET /api/v1/pay-runs should list pay runs."""
        response = await client.get(
            "/api/v1/pay-runs",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1  # At least the seeded pay run

    async def test_list_pay_runs_with_status_filter(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """Can filter pay runs by status."""
        response = await client.get(
            "/api/v1/pay-runs",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            params={"status": "draft"},
        )

        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["status"] == "draft"

    async def test_get_pay_run(self, client: AsyncClient, seeded_db: AsyncSession):
        """GET /api/v1/pay-runs/{id} should return specific pay run."""
        response = await client.get(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(DRAFT_PAY_RUN_ID)

    async def test_get_pay_run_not_found(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """Getting non-existent pay run should return 404."""
        response = await client.get(
            f"/api/v1/pay-runs/{uuid4()}",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 404

    async def test_list_pay_run_employees(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """GET /api/v1/pay-runs/{id}/employees should list employees."""
        response = await client.get(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/employees",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["total"] == 2  # Alice and Bob


class TestPayRunStateTransitions:
    """Test pay run state transition endpoints."""

    async def test_preview_pay_run(self, client: AsyncClient, seeded_db: AsyncSession):
        """POST /api/v1/pay-runs/{id}/preview should compute preview."""
        response = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "preview"
        assert "calculation_id" in data
        assert "employees" in data
        assert len(data["employees"]) == 2

        # Check totals
        assert Decimal(data["total_gross"]) > 0
        assert Decimal(data["total_net"]) > 0

    async def test_preview_is_idempotent(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """Calling preview twice should return same calculation_id."""
        response1 = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )
        response2 = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()
        assert data1["calculation_id"] == data2["calculation_id"]

    async def test_approve_pay_run(self, client: AsyncClient, seeded_db: AsyncSession):
        """POST /api/v1/pay-runs/{id}/approve should approve the run."""
        # First preview
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        # Then approve
        response = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/approve",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={"approver_id": str(uuid4())},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "approved"
        assert "approved_at" in data
        assert "inputs_locked" in data

    async def test_commit_pay_run(self, client: AsyncClient, seeded_db: AsyncSession):
        """POST /api/v1/pay-runs/{id}/commit should commit the run."""
        # Preview -> Approve -> Commit
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/approve",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={"approver_id": str(uuid4())},
        )

        response = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/commit",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "committed"
        assert data["statements_created"] >= 1
        assert data["line_items_created"] >= 1

    async def test_reopen_approved_pay_run(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """POST /api/v1/pay-runs/{id}/reopen should reopen approved run."""
        # Preview -> Approve
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/approve",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={"approver_id": str(uuid4())},
        )

        # Reopen
        response = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/reopen",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "preview"
        assert "inputs_unlocked" in data

    async def test_cannot_reopen_committed_run(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """Cannot reopen a committed pay run."""
        # Full cycle to committed
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/approve",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={"approver_id": str(uuid4())},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/commit",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        # Try to reopen
        response = await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/reopen",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 400
        assert "committed" in response.json()["detail"].lower()


class TestPayStatements:
    """Test pay statement endpoints."""

    async def test_list_statements_after_commit(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """GET /api/v1/pay-runs/{id}/statements after commit."""
        # Commit the run
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/preview",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/approve",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
            json={"approver_id": str(uuid4())},
        )
        await client.post(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/commit",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        # List statements
        response = await client.get(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/statements",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2  # Alice and Bob

        for stmt in data:
            assert "gross_pay" in stmt
            assert "net_pay" in stmt
            assert "line_items" in stmt
            assert len(stmt["line_items"]) > 0

    async def test_cannot_list_statements_before_commit(
        self, client: AsyncClient, seeded_db: AsyncSession
    ):
        """Listing statements on non-committed run should fail."""
        response = await client.get(
            f"/api/v1/pay-runs/{DRAFT_PAY_RUN_ID}/statements",
            headers={"X-Tenant-ID": str(DEMO_TENANT_ID)},
        )

        assert response.status_code == 400
        assert "committed" in response.json()["detail"].lower()
