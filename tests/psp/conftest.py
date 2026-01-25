"""PSP test fixtures with database setup."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from payroll_engine.config import settings


# Test database URLs
TEST_DATABASE_URL_ASYNC = settings.database_url.replace("payroll_dev", "payroll_test")
TEST_DATABASE_URL_SYNC = TEST_DATABASE_URL_ASYNC.replace("postgresql+asyncpg", "postgresql")


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session-scoped fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    """Create async test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL_ASYNC, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def sync_engine():
    """Create sync test database engine."""
    engine = create_engine(TEST_DATABASE_URL_SYNC, echo=False)
    yield engine
    engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_db(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Get async database session for tests."""
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="function")
def sync_db(sync_engine) -> Generator[Session, None, None]:
    """Get sync database session for tests."""
    with Session(sync_engine) as session:
        yield session
        session.rollback()


@pytest_asyncio.fixture(scope="function")
async def psp_db(async_db: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Clean PSP tables before each test."""
    # Truncate PSP tables in dependency order
    psp_tables = [
        "psp_settlement_link",
        "psp_settlement_event",
        "psp_reservation",
        "psp_ledger_entry",
        "psp_ledger_account",
        "psp_bank_account",
        "funding_gate_evaluation",
        "funding_event",
        "funding_request",
        "payment_attempt",
        "payment_instruction",
        "third_party_obligation",
        "tax_liability",
    ]

    for table in psp_tables:
        try:
            await async_db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception:
            pass  # Table may not exist

    await async_db.commit()
    yield async_db


@pytest.fixture(scope="function")
def psp_sync_db(sync_db: Session) -> Generator[Session, None, None]:
    """Clean PSP tables before each test (sync version)."""
    psp_tables = [
        "psp_settlement_link",
        "psp_settlement_event",
        "psp_reservation",
        "psp_ledger_entry",
        "psp_ledger_account",
        "psp_bank_account",
        "funding_gate_evaluation",
        "funding_event",
        "funding_request",
        "payment_attempt",
        "payment_instruction",
        "third_party_obligation",
        "tax_liability",
    ]

    for table in psp_tables:
        try:
            sync_db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception:
            pass

    sync_db.commit()
    yield sync_db


# Test data generators
class PSPTestData:
    """Test data generator for PSP tests."""

    def __init__(self):
        self.tenant_id = uuid4()
        self.legal_entity_id = uuid4()

    def create_bank_account(self, db: Session) -> UUID:
        """Create a test bank account."""
        result = db.execute(
            text("""
                INSERT INTO psp_bank_account(tenant_id, bank_name, bank_account_ref_token, rail_support_json)
                VALUES (:tenant_id, 'Amegy Bank', :token, '{"ach": true, "wire": true}'::jsonb)
                RETURNING psp_bank_account_id
            """),
            {"tenant_id": str(self.tenant_id), "token": f"token_{uuid4().hex[:8]}"},
        )
        return UUID(str(result.scalar()))

    def create_ledger_accounts(self, db: Session) -> dict[str, UUID]:
        """Create standard ledger accounts."""
        account_types = [
            "client_funding_clearing",
            "client_net_pay_payable",
            "client_tax_impound_payable",
            "client_third_party_payable",
            "psp_settlement_clearing",
            "psp_fees_revenue",
        ]
        accounts = {}
        for acct_type in account_types:
            result = db.execute(
                text("""
                    INSERT INTO psp_ledger_account(tenant_id, legal_entity_id, account_type, currency)
                    VALUES (:tenant_id, :legal_entity_id, :account_type, 'USD')
                    ON CONFLICT (tenant_id, legal_entity_id, account_type, currency) DO UPDATE
                    SET status = 'active'
                    RETURNING psp_ledger_account_id
                """),
                {
                    "tenant_id": str(self.tenant_id),
                    "legal_entity_id": str(self.legal_entity_id),
                    "account_type": acct_type,
                },
            )
            accounts[acct_type] = UUID(str(result.scalar()))
        db.commit()
        return accounts

    async def create_bank_account_async(self, db: AsyncSession) -> UUID:
        """Create a test bank account (async)."""
        result = await db.execute(
            text("""
                INSERT INTO psp_bank_account(tenant_id, bank_name, bank_account_ref_token, rail_support_json)
                VALUES (:tenant_id, 'Amegy Bank', :token, '{"ach": true, "wire": true}'::jsonb)
                RETURNING psp_bank_account_id
            """),
            {"tenant_id": str(self.tenant_id), "token": f"token_{uuid4().hex[:8]}"},
        )
        return UUID(str(result.scalar()))

    async def create_ledger_accounts_async(self, db: AsyncSession) -> dict[str, UUID]:
        """Create standard ledger accounts (async)."""
        account_types = [
            "client_funding_clearing",
            "client_net_pay_payable",
            "client_tax_impound_payable",
            "client_third_party_payable",
            "psp_settlement_clearing",
            "psp_fees_revenue",
        ]
        accounts = {}
        for acct_type in account_types:
            result = await db.execute(
                text("""
                    INSERT INTO psp_ledger_account(tenant_id, legal_entity_id, account_type, currency)
                    VALUES (:tenant_id, :legal_entity_id, :account_type, 'USD')
                    ON CONFLICT (tenant_id, legal_entity_id, account_type, currency) DO UPDATE
                    SET status = 'active'
                    RETURNING psp_ledger_account_id
                """),
                {
                    "tenant_id": str(self.tenant_id),
                    "legal_entity_id": str(self.legal_entity_id),
                    "account_type": acct_type,
                },
            )
            accounts[acct_type] = UUID(str(result.scalar()))
        await db.commit()
        return accounts


@pytest.fixture
def test_data() -> PSPTestData:
    """Create test data generator."""
    return PSPTestData()
