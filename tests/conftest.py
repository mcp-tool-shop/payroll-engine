"""Pytest fixtures for payroll engine tests."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from payroll_engine.models import (
    Address,
    Base,
    DeductionCode,
    EarningCode,
    Employee,
    EmployeeDeduction,
    EmployeeTaxProfile,
    Employment,
    Jurisdiction,
    LegalEntity,
    PayPeriod,
    PayRate,
    PayRun,
    PayRunEmployee,
    PaySchedule,
    PayrollRule,
    PayrollRuleVersion,
    Person,
    Tenant,
    TimeEntry,
)

# Use in-memory SQLite for tests (with async support)
# For full Postgres features, use a test Postgres database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    """Create test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for each test."""
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def test_tenant(session: AsyncSession) -> Tenant:
    """Create a test tenant."""
    tenant = Tenant(
        tenant_id=uuid4(),
        name="Test Company",
        status="active",
    )
    session.add(tenant)
    await session.flush()
    return tenant


@pytest.fixture
async def test_address(session: AsyncSession) -> Address:
    """Create a test address."""
    address = Address(
        address_id=uuid4(),
        line1="123 Main St",
        city="San Francisco",
        state="CA",
        postal_code="94102",
        country="US",
    )
    session.add(address)
    await session.flush()
    return address


@pytest.fixture
async def test_legal_entity(
    session: AsyncSession, test_tenant: Tenant, test_address: Address
) -> LegalEntity:
    """Create a test legal entity."""
    entity = LegalEntity(
        legal_entity_id=uuid4(),
        tenant_id=test_tenant.tenant_id,
        legal_name="Test Corp",
        ein="12-3456789",
        address_id=test_address.address_id,
    )
    session.add(entity)
    await session.flush()
    return entity


@pytest.fixture
async def test_jurisdictions(session: AsyncSession) -> dict[str, Jurisdiction]:
    """Create test jurisdictions."""
    fed = Jurisdiction(
        jurisdiction_id=uuid4(),
        jurisdiction_type="FED",
        code="FED",
        name="Federal",
    )
    ca = Jurisdiction(
        jurisdiction_id=uuid4(),
        jurisdiction_type="STATE",
        code="CA",
        name="California",
        parent_jurisdiction_id=fed.jurisdiction_id,
    )
    session.add_all([fed, ca])
    await session.flush()
    return {"FED": fed, "CA": ca}


@pytest.fixture
async def test_earning_codes(
    session: AsyncSession, test_legal_entity: LegalEntity
) -> dict[str, EarningCode]:
    """Create test earning codes."""
    regular = EarningCode(
        earning_code_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        code="REG",
        name="Regular Pay",
        earning_category="regular",
        is_taxable_federal=True,
        is_taxable_state_default=True,
        is_taxable_local_default=True,
    )
    overtime = EarningCode(
        earning_code_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        code="OT",
        name="Overtime Pay",
        earning_category="overtime",
        is_taxable_federal=True,
        is_taxable_state_default=True,
        is_taxable_local_default=True,
    )
    bonus = EarningCode(
        earning_code_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        code="BONUS",
        name="Bonus",
        earning_category="bonus",
        is_taxable_federal=True,
        is_taxable_state_default=True,
        is_taxable_local_default=True,
    )
    session.add_all([regular, overtime, bonus])
    await session.flush()
    return {"REG": regular, "OT": overtime, "BONUS": bonus}


@pytest.fixture
async def test_deduction_codes(
    session: AsyncSession, test_legal_entity: LegalEntity
) -> dict[str, DeductionCode]:
    """Create test deduction codes."""
    pretax_401k = DeductionCode(
        deduction_code_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        code="401K",
        name="401(k) Contribution",
        deduction_type="pretax",
        calc_method="percent",
        is_employer_match_eligible=True,
    )
    posttax_parking = DeductionCode(
        deduction_code_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        code="PARK",
        name="Parking",
        deduction_type="posttax",
        calc_method="flat",
        is_employer_match_eligible=False,
    )
    session.add_all([pretax_401k, posttax_parking])
    await session.flush()
    return {"401K": pretax_401k, "PARK": posttax_parking}


@pytest.fixture
async def test_employees(
    session: AsyncSession,
    test_tenant: Tenant,
    test_legal_entity: LegalEntity,
    test_earning_codes: dict[str, EarningCode],
    test_deduction_codes: dict[str, DeductionCode],
    test_jurisdictions: dict[str, Jurisdiction],
) -> list[Employee]:
    """Create test employees with rates, deductions, and tax profiles."""
    employees = []

    for i, (name, hourly_rate, has_401k) in enumerate([
        ("Alice Smith", Decimal("25.00"), True),
        ("Bob Jones", Decimal("30.00"), False),
    ], start=1):
        first_name, last_name = name.split()

        # Create person
        person = Person(
            person_id=uuid4(),
            tenant_id=test_tenant.tenant_id,
            first_name=first_name,
            last_name=last_name,
            email=f"{first_name.lower()}@test.com",
        )
        session.add(person)
        await session.flush()

        # Create employee
        employee = Employee(
            employee_id=uuid4(),
            tenant_id=test_tenant.tenant_id,
            person_id=person.person_id,
            employee_number=f"EMP{i:03d}",
            status="active",
            primary_legal_entity_id=test_legal_entity.legal_entity_id,
            hire_date=date(2023, 1, 1),
        )
        session.add(employee)
        await session.flush()

        # Create employment
        employment = Employment(
            employment_id=uuid4(),
            employee_id=employee.employee_id,
            legal_entity_id=test_legal_entity.legal_entity_id,
            start_date=date(2023, 1, 1),
            worker_type="w2",
            pay_type="hourly",
            flsa_status="nonexempt",
        )
        session.add(employment)

        # Create pay rate
        rate = PayRate(
            pay_rate_id=uuid4(),
            employee_id=employee.employee_id,
            start_date=date(2023, 1, 1),
            rate_type="hourly",
            amount=hourly_rate,
            priority=0,
        )
        session.add(rate)

        # Create deductions
        if has_401k:
            ded = EmployeeDeduction(
                employee_deduction_id=uuid4(),
                employee_id=employee.employee_id,
                deduction_code_id=test_deduction_codes["401K"].deduction_code_id,
                start_date=date(2023, 1, 1),
                employee_percent=Decimal("6.00"),  # 6% contribution
            )
            session.add(ded)

        # Create tax profiles
        fed_profile = EmployeeTaxProfile(
            employee_tax_profile_id=uuid4(),
            employee_id=employee.employee_id,
            jurisdiction_id=test_jurisdictions["FED"].jurisdiction_id,
            filing_status="single",
            allowances=1,
            effective_start=date(2023, 1, 1),
        )
        ca_profile = EmployeeTaxProfile(
            employee_tax_profile_id=uuid4(),
            employee_id=employee.employee_id,
            jurisdiction_id=test_jurisdictions["CA"].jurisdiction_id,
            filing_status="single",
            residency_status="resident",
            effective_start=date(2023, 1, 1),
        )
        session.add_all([fed_profile, ca_profile])

        employees.append(employee)

    await session.flush()
    return employees


@pytest.fixture
async def test_pay_schedule(
    session: AsyncSession, test_legal_entity: LegalEntity
) -> PaySchedule:
    """Create a test pay schedule."""
    schedule = PaySchedule(
        pay_schedule_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        name="Biweekly",
        frequency="biweekly",
        pay_day_rule="friday",
    )
    session.add(schedule)
    await session.flush()
    return schedule


@pytest.fixture
async def test_pay_period(
    session: AsyncSession, test_pay_schedule: PaySchedule
) -> PayPeriod:
    """Create a test pay period."""
    period = PayPeriod(
        pay_period_id=uuid4(),
        pay_schedule_id=test_pay_schedule.pay_schedule_id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 14),
        check_date=date(2024, 1, 19),
        status="open",
    )
    session.add(period)
    await session.flush()
    return period


@pytest.fixture
async def test_pay_run(
    session: AsyncSession,
    test_legal_entity: LegalEntity,
    test_pay_period: PayPeriod,
    test_employees: list[Employee],
) -> PayRun:
    """Create a test pay run with employees."""
    pay_run = PayRun(
        pay_run_id=uuid4(),
        legal_entity_id=test_legal_entity.legal_entity_id,
        pay_period_id=test_pay_period.pay_period_id,
        run_type="regular",
        status="draft",
    )
    session.add(pay_run)
    await session.flush()

    # Add employees to run
    for emp in test_employees:
        pre = PayRunEmployee(
            pay_run_employee_id=uuid4(),
            pay_run_id=pay_run.pay_run_id,
            employee_id=emp.employee_id,
            status="included",
            calculation_version="1.0.0",
        )
        session.add(pre)

    await session.flush()
    return pay_run


@pytest.fixture
async def test_time_entries(
    session: AsyncSession,
    test_employees: list[Employee],
    test_earning_codes: dict[str, EarningCode],
    test_pay_period: PayPeriod,
) -> list[TimeEntry]:
    """Create test time entries."""
    entries = []

    for emp in test_employees:
        # Regular hours for each day of the period
        for day_offset in range(14):
            work_date = test_pay_period.period_start + timedelta(days=day_offset)

            # Skip weekends
            if work_date.weekday() >= 5:
                continue

            entry = TimeEntry(
                time_entry_id=uuid4(),
                employee_id=emp.employee_id,
                work_date=work_date,
                earning_code_id=test_earning_codes["REG"].earning_code_id,
                hours=Decimal("8.00"),
                source_system="manual",
            )
            entries.append(entry)
            session.add(entry)

    await session.flush()
    return entries


@pytest.fixture
async def test_tax_rules(
    session: AsyncSession, test_jurisdictions: dict[str, Jurisdiction]
) -> dict[str, PayrollRuleVersion]:
    """Create test tax rules."""
    rules = {}

    # Federal income tax (simplified)
    fed_income_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="federal_income_tax",
        rule_type="tax",
    )
    fed_income_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=fed_income_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.irs.gov",
        source_last_verified_at=datetime.utcnow(),
        logic_hash="fed_income_v1",
        payload_json={
            "tax_type": "federal_income",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [
                {"min": 0, "max": 11600, "rate": 0.10, "flat": 0},
                {"min": 11600, "max": 47150, "rate": 0.12, "flat": 1160},
                {"min": 47150, "max": 100525, "rate": 0.22, "flat": 5426},
            ],
        },
    )
    session.add_all([fed_income_rule, fed_income_version])
    rules["federal_income_tax"] = fed_income_version

    # Social Security (employee)
    ss_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="social_security_employee",
        rule_type="tax",
    )
    ss_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ss_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov",
        source_last_verified_at=datetime.utcnow(),
        logic_hash="ss_ee_v1",
        payload_json={
            "tax_type": "social_security",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.062, "flat": 0}],
            "wage_base_limit": 168600,
            "is_employer_tax": False,
        },
    )
    session.add_all([ss_rule, ss_version])
    rules["social_security_employee"] = ss_version

    # Social Security (employer)
    ss_er_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="social_security_employer",
        rule_type="tax",
    )
    ss_er_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ss_er_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov",
        source_last_verified_at=datetime.utcnow(),
        logic_hash="ss_er_v1",
        payload_json={
            "tax_type": "social_security",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.062, "flat": 0}],
            "wage_base_limit": 168600,
            "is_employer_tax": True,
        },
    )
    session.add_all([ss_er_rule, ss_er_version])
    rules["social_security_employer"] = ss_er_version

    # Medicare (employee)
    med_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="medicare_employee",
        rule_type="tax",
    )
    med_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=med_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov",
        source_last_verified_at=datetime.utcnow(),
        logic_hash="med_ee_v1",
        payload_json={
            "tax_type": "medicare",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.0145, "flat": 0}],
            "is_employer_tax": False,
        },
    )
    session.add_all([med_rule, med_version])
    rules["medicare_employee"] = med_version

    # Medicare (employer)
    med_er_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="medicare_employer",
        rule_type="tax",
    )
    med_er_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=med_er_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov",
        source_last_verified_at=datetime.utcnow(),
        logic_hash="med_er_v1",
        payload_json={
            "tax_type": "medicare",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.0145, "flat": 0}],
            "is_employer_tax": True,
        },
    )
    session.add_all([med_er_rule, med_er_version])
    rules["medicare_employer"] = med_er_version

    await session.flush()
    return rules
