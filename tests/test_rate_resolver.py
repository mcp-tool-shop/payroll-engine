"""Tests for pay rate resolver."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from payroll_engine.calculators.rate_resolver import RateNotFoundError, RateResolver
from payroll_engine.models import PayRate


class TestRateResolver:
    """Test rate resolution with dimensional matching."""

    @pytest.mark.asyncio
    async def test_resolve_rate_for_employee_simple(
        self, session, test_employees
    ):
        """Test resolving simple rate without dimensions."""
        resolver = RateResolver(session)
        employee = test_employees[0]  # Alice with $25/hr

        rate = await resolver.resolve_rate_for_employee(
            employee_id=employee.employee_id,
            as_of_date=date(2024, 1, 15),
        )

        assert rate == Decimal("25.00")

    @pytest.mark.asyncio
    async def test_resolve_rate_not_found(self, session):
        """Test error when no rate found."""
        resolver = RateResolver(session)
        fake_employee_id = uuid4()

        with pytest.raises(RateNotFoundError) as exc_info:
            await resolver.resolve_rate_for_employee(
                employee_id=fake_employee_id,
                as_of_date=date(2024, 1, 15),
            )

        assert exc_info.value.employee_id == fake_employee_id

    @pytest.mark.asyncio
    async def test_resolve_rate_respects_effective_dates(
        self, session, test_employees
    ):
        """Test that rate resolution respects effective dates."""
        resolver = RateResolver(session)
        employee = test_employees[0]

        # Add a future rate
        future_rate = PayRate(
            pay_rate_id=uuid4(),
            employee_id=employee.employee_id,
            start_date=date(2024, 7, 1),
            rate_type="hourly",
            amount=Decimal("30.00"),
            priority=0,
        )
        session.add(future_rate)
        await session.flush()

        # Before future rate effective date
        rate_before = await resolver.resolve_rate_for_employee(
            employee_id=employee.employee_id,
            as_of_date=date(2024, 1, 15),
        )
        assert rate_before == Decimal("25.00")

        # After future rate effective date
        rate_after = await resolver.resolve_rate_for_employee(
            employee_id=employee.employee_id,
            as_of_date=date(2024, 7, 15),
        )
        assert rate_after == Decimal("30.00")

    @pytest.mark.asyncio
    async def test_dimensional_matching_priority(
        self, session, test_employees, test_legal_entity
    ):
        """Test that more specific dimensional matches win."""
        from payroll_engine.models import Job

        resolver = RateResolver(session)
        employee = test_employees[0]

        # Create a job
        job = Job(
            job_id=uuid4(),
            legal_entity_id=test_legal_entity.legal_entity_id,
            job_code="SENIOR",
            title="Senior Developer",
        )
        session.add(job)
        await session.flush()

        # Add job-specific rate (higher rate for senior job)
        job_rate = PayRate(
            pay_rate_id=uuid4(),
            employee_id=employee.employee_id,
            start_date=date(2023, 1, 1),
            rate_type="hourly",
            amount=Decimal("35.00"),
            job_id=job.job_id,
            priority=0,
        )
        session.add(job_rate)
        await session.flush()

        # Without job dimension, should get base rate
        rate_no_job = await resolver.resolve_rate_for_employee(
            employee_id=employee.employee_id,
            as_of_date=date(2024, 1, 15),
        )
        assert rate_no_job == Decimal("25.00")

        # With matching job dimension, should get job-specific rate
        rate_with_job = await resolver.resolve_rate_for_employee(
            employee_id=employee.employee_id,
            as_of_date=date(2024, 1, 15),
            job_id=job.job_id,
        )
        assert rate_with_job == Decimal("35.00")


class TestPayRateModel:
    """Test PayRate model methods."""

    def test_is_active_on(self):
        """Test effective date checking."""
        rate = PayRate(
            pay_rate_id=uuid4(),
            employee_id=uuid4(),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            rate_type="hourly",
            amount=Decimal("25.00"),
        )

        # Before start
        assert rate.is_active_on(date(2023, 12, 31)) is False

        # On start date
        assert rate.is_active_on(date(2024, 1, 1)) is True

        # During period
        assert rate.is_active_on(date(2024, 3, 15)) is True

        # On end date
        assert rate.is_active_on(date(2024, 6, 30)) is True

        # After end
        assert rate.is_active_on(date(2024, 7, 1)) is False

    def test_is_active_on_no_end_date(self):
        """Test effective date with no end date."""
        rate = PayRate(
            pay_rate_id=uuid4(),
            employee_id=uuid4(),
            start_date=date(2024, 1, 1),
            end_date=None,
            rate_type="hourly",
            amount=Decimal("25.00"),
        )

        # Far in the future should still be active
        assert rate.is_active_on(date(2030, 12, 31)) is True

    def test_matches_dimensions(self):
        """Test dimensional matching scoring."""
        job_id = uuid4()
        dept_id = uuid4()

        # Generic rate (no dimensions)
        rate_generic = PayRate(
            pay_rate_id=uuid4(),
            employee_id=uuid4(),
            start_date=date(2024, 1, 1),
            rate_type="hourly",
            amount=Decimal("20.00"),
        )
        score_generic = rate_generic.matches_dimensions(
            job_id=job_id, project_id=None, department_id=dept_id, worksite_id=None
        )
        assert score_generic == 0  # No dimensions to match

        # Job-specific rate
        rate_job = PayRate(
            pay_rate_id=uuid4(),
            employee_id=uuid4(),
            start_date=date(2024, 1, 1),
            rate_type="hourly",
            amount=Decimal("25.00"),
            job_id=job_id,
        )
        score_job_match = rate_job.matches_dimensions(
            job_id=job_id, project_id=None, department_id=None, worksite_id=None
        )
        assert score_job_match == 8  # Job match = +8

        # Job mismatch
        score_job_mismatch = rate_job.matches_dimensions(
            job_id=uuid4(), project_id=None, department_id=None, worksite_id=None
        )
        assert score_job_mismatch == -1  # Explicit mismatch

        # Multi-dimensional rate
        rate_multi = PayRate(
            pay_rate_id=uuid4(),
            employee_id=uuid4(),
            start_date=date(2024, 1, 1),
            rate_type="hourly",
            amount=Decimal("30.00"),
            job_id=job_id,
            department_id=dept_id,
        )
        score_multi = rate_multi.matches_dimensions(
            job_id=job_id, project_id=None, department_id=dept_id, worksite_id=None
        )
        assert score_multi == 10  # Job (8) + Dept (2)
