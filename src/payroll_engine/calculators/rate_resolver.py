"""Pay rate resolution with dimensional matching."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models import PayRate

if TYPE_CHECKING:
    from payroll_engine.models import TimeEntry


class RateNotFoundError(Exception):
    """Raised when no matching rate is found."""

    def __init__(
        self,
        employee_id: UUID,
        as_of_date: date,
        dimensions: dict[str, UUID | None],
    ):
        self.employee_id = employee_id
        self.as_of_date = as_of_date
        self.dimensions = dimensions
        super().__init__(
            f"No matching pay rate found for employee {employee_id} "
            f"on {as_of_date} with dimensions {dimensions}"
        )


class RateResolver:
    """Resolves pay rates using dimensional matching.

    Rate selection priority:
    1. If time entry has rate_override, use it
    2. Select from pay_rate table using:
       - Matching dimensions (job/project/department/worksite)
       - Most specific match wins (more dimensions matched = higher score)
       - Priority tie-breaker
       - Effective date range must include as_of_date
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve_rate_for_time_entry(
        self,
        time_entry: TimeEntry,
        as_of_date: date,
    ) -> Decimal:
        """Resolve the pay rate for a time entry.

        Args:
            time_entry: The time entry to resolve rate for
            as_of_date: The effective date for rate lookup

        Returns:
            The resolved rate amount

        Raises:
            RateNotFoundError: If no matching rate is found
        """
        # Check for rate override first
        if time_entry.rate_override is not None:
            return time_entry.rate_override

        # Get all candidate rates for the employee
        rates = await self._get_candidate_rates(time_entry.employee_id, as_of_date)

        if not rates:
            raise RateNotFoundError(
                time_entry.employee_id,
                as_of_date,
                {
                    "job_id": time_entry.job_id,
                    "project_id": time_entry.project_id,
                    "department_id": time_entry.department_id,
                    "worksite_id": time_entry.worksite_id,
                },
            )

        # Score each rate by dimension match
        best_rate: PayRate | None = None
        best_score = -1
        best_priority = -1

        for rate in rates:
            score = rate.matches_dimensions(
                job_id=time_entry.job_id,
                project_id=time_entry.project_id,
                department_id=time_entry.department_id,
                worksite_id=time_entry.worksite_id,
            )

            if score < 0:
                # Explicit mismatch, skip
                continue

            # Higher score wins, then higher priority
            if score > best_score or (score == best_score and rate.priority > best_priority):
                best_rate = rate
                best_score = score
                best_priority = rate.priority

        if best_rate is None:
            raise RateNotFoundError(
                time_entry.employee_id,
                as_of_date,
                {
                    "job_id": time_entry.job_id,
                    "project_id": time_entry.project_id,
                    "department_id": time_entry.department_id,
                    "worksite_id": time_entry.worksite_id,
                },
            )

        return best_rate.amount

    async def resolve_rate_for_employee(
        self,
        employee_id: UUID,
        as_of_date: date,
        job_id: UUID | None = None,
        project_id: UUID | None = None,
        department_id: UUID | None = None,
        worksite_id: UUID | None = None,
    ) -> Decimal:
        """Resolve the pay rate for an employee with optional dimensions."""
        rates = await self._get_candidate_rates(employee_id, as_of_date)

        if not rates:
            raise RateNotFoundError(
                employee_id,
                as_of_date,
                {
                    "job_id": job_id,
                    "project_id": project_id,
                    "department_id": department_id,
                    "worksite_id": worksite_id,
                },
            )

        best_rate: PayRate | None = None
        best_score = -1
        best_priority = -1

        for rate in rates:
            score = rate.matches_dimensions(
                job_id=job_id,
                project_id=project_id,
                department_id=department_id,
                worksite_id=worksite_id,
            )

            if score < 0:
                continue

            if score > best_score or (score == best_score and rate.priority > best_priority):
                best_rate = rate
                best_score = score
                best_priority = rate.priority

        if best_rate is None:
            raise RateNotFoundError(
                employee_id,
                as_of_date,
                {
                    "job_id": job_id,
                    "project_id": project_id,
                    "department_id": department_id,
                    "worksite_id": worksite_id,
                },
            )

        return best_rate.amount

    async def _get_candidate_rates(
        self,
        employee_id: UUID,
        as_of_date: date,
    ) -> list[PayRate]:
        """Get all candidate rates for an employee effective on a date."""
        result = await self.session.execute(
            select(PayRate).where(
                PayRate.employee_id == employee_id,
                PayRate.start_date <= as_of_date,
                (PayRate.end_date.is_(None) | (PayRate.end_date >= as_of_date)),
            )
        )
        return list(result.scalars().all())
