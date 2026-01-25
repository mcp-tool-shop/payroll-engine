"""Integration test fixtures with real database."""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from payroll_engine.api.app import create_app
from payroll_engine.config import settings
from payroll_engine.database import Base, async_session_factory


# Test database URL - use test database
TEST_DATABASE_URL = settings.database_url.replace("payroll_dev", "payroll_test")


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session-scoped fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Get database session for integration tests."""
    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def clean_db(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Clean database before each test by truncating relevant tables."""
    # Truncate in reverse dependency order
    tables_to_truncate = [
        "gl_entry",
        "payment_batch",
        "pay_line_item",
        "pay_statement",
        "pay_run_employee",
        "pay_run",
        "time_entry",
        "pay_input_adjustment",
        "employee_deduction",
        "employee_tax_profile",
        "payroll_rule_version",
        "payroll_rule",
        "pay_rate",
        "employment",
        "employee",
        "pay_schedule",
        "cost_center",
        "department",
        "job",
        "worksite",
        "legal_entity",
        "tenant",
    ]

    for table in tables_to_truncate:
        try:
            await db_session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception:
            pass  # Table may not exist

    await db_session.commit()
    yield db_session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create async HTTP client for API testing."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# Fixture UUIDs from seed_minimal.sql
DEMO_TENANT_ID = UUID("adfb6898-026f-fa17-8583-404672c7972a")
DEMO_LEGAL_ENTITY_ID = UUID("b2d1e6f0-1234-5678-9abc-def012345678")
DEMO_PAY_SCHEDULE_ID = UUID("c3e2f7a1-2345-6789-abcd-ef0123456789")
ALICE_EMPLOYEE_ID = UUID("e5a4c9d3-4567-89ab-cdef-012345678901")
BOB_EMPLOYEE_ID = UUID("f6b5dae4-5678-9abc-def0-123456789012")
ALICE_EMPLOYMENT_ID = UUID("a1b2c3d4-1111-2222-3333-444455556666")
BOB_EMPLOYMENT_ID = UUID("b2c3d4e5-2222-3333-4444-555566667777")
DRAFT_PAY_RUN_ID = UUID("d4c3b2a1-9876-5432-1fed-cba098765432")
ALICE_TIME_ENTRY_ID = UUID("11111111-aaaa-bbbb-cccc-dddddddddddd")
ALICE_BONUS_ADJ_ID = UUID("22222222-eeee-ffff-0000-111111111111")


@pytest_asyncio.fixture
async def seeded_db(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Load seed_minimal.sql fixture data."""
    # Read and execute seed file
    seed_path = "F:/payroll-engine/phase1_pack_additions/fixtures/seed_minimal.sql"
    with open(seed_path, "r") as f:
        seed_sql = f.read()

    # Execute seed SQL - split by statements
    statements = [s.strip() for s in seed_sql.split(";") if s.strip()]
    for stmt in statements:
        if stmt and not stmt.startswith("--"):
            try:
                await db_session.execute(text(stmt))
            except Exception as e:
                # Skip comments and empty statements
                if "syntax error" not in str(e).lower():
                    pass

    await db_session.commit()
    yield db_session
