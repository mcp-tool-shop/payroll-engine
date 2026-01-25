"""Employee and employment models."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from payroll_engine.models.company import (
        Address,
        Department,
        Job,
        LegalEntity,
        Tenant,
        Worksite,
    )
    from payroll_engine.models.payroll import (
        EmployeeDeduction,
        EmployeeTaxProfile,
        GarnishmentOrder,
        PayRate,
        TimeEntry,
    )


class Person(Base, TimestampMixin):
    """Person (identity) separate from employment."""

    __tablename__ = "person"

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    ssn_last4: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    employees: Mapped[list[Employee]] = relationship(back_populates="person")

    @property
    def full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.last_name}"


class Employee(Base, TimestampMixin):
    """Employee record."""

    __tablename__ = "employee"

    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("person.person_id", ondelete="RESTRICT"),
        nullable=False,
    )
    employee_number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    primary_legal_entity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id"),
        nullable=True,
    )
    home_address_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("address.address_id"),
        nullable=True,
    )
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_number", name="employee_tenant_number_unique"),
        CheckConstraint(
            "status IN ('active', 'terminated', 'on_leave')",
            name="employee_status_check",
        ),
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(back_populates="employees")
    person: Mapped[Person] = relationship(back_populates="employees")
    primary_legal_entity: Mapped[LegalEntity | None] = relationship()
    home_address: Mapped[Address | None] = relationship()
    employments: Mapped[list[Employment]] = relationship(back_populates="employee")
    pay_rates: Mapped[list[PayRate]] = relationship(back_populates="employee")
    deductions: Mapped[list[EmployeeDeduction]] = relationship(back_populates="employee")
    tax_profiles: Mapped[list[EmployeeTaxProfile]] = relationship(back_populates="employee")
    garnishments: Mapped[list[GarnishmentOrder]] = relationship(back_populates="employee")
    time_entries: Mapped[list[TimeEntry]] = relationship(back_populates="employee")


class Employment(Base, TimestampMixin):
    """Employment relationship between employee and legal entity."""

    __tablename__ = "employment"

    employment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    worker_type: Mapped[str] = mapped_column(String, nullable=False, default="w2")
    pay_type: Mapped[str] = mapped_column(String, nullable=False)
    flsa_status: Mapped[str] = mapped_column(String, nullable=False)
    primary_worksite_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worksite.worksite_id"),
        nullable=True,
    )
    primary_department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    primary_job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.job_id"),
        nullable=True,
    )
    manager_employee_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("worker_type IN ('w2')", name="employment_worker_type_check"),
        CheckConstraint("pay_type IN ('hourly', 'salary')", name="employment_pay_type_check"),
        CheckConstraint(
            "flsa_status IN ('exempt', 'nonexempt')",
            name="employment_flsa_status_check",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="employment_dates_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(
        back_populates="employments",
        foreign_keys=[employee_id],
    )
    legal_entity: Mapped[LegalEntity] = relationship()
    primary_worksite: Mapped[Worksite | None] = relationship()
    primary_department: Mapped[Department | None] = relationship()
    primary_job: Mapped[Job | None] = relationship()
    manager: Mapped[Employee | None] = relationship(foreign_keys=[manager_employee_id])

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if employment is active on a given date."""
        if self.start_date > as_of_date:
            return False
        if self.end_date is not None and self.end_date < as_of_date:
            return False
        return True
