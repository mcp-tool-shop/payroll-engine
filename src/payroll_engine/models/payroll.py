"""Payroll run, statement, and line item models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from payroll_engine.models.company import Department, Job, LegalEntity, Project, Worksite
    from payroll_engine.models.employee import Employee


# ===== Pay Schedules & Periods =====


class PaySchedule(Base, TimestampMixin):
    """Pay schedule definition."""

    __tablename__ = "pay_schedule"

    pay_schedule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    frequency: Mapped[str] = mapped_column(String, nullable=False)
    pay_day_rule: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "name", name="pay_schedule_le_name_unique"),
        CheckConstraint(
            "frequency IN ('weekly', 'biweekly', 'semimonthly', 'monthly')",
            name="pay_schedule_frequency_check",
        ),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()
    periods: Mapped[list[PayPeriod]] = relationship(back_populates="pay_schedule")


class PayPeriod(Base, TimestampMixin):
    """Pay period instance."""

    __tablename__ = "pay_period"

    pay_period_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_schedule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_schedule.pay_schedule_id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    check_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")

    __table_args__ = (
        UniqueConstraint(
            "pay_schedule_id",
            "period_start",
            "period_end",
            name="pay_period_schedule_dates_unique",
        ),
        CheckConstraint(
            "status IN ('open', 'locked', 'paid', 'voided')",
            name="pay_period_status_check",
        ),
        CheckConstraint("period_end >= period_start", name="pay_period_dates_check"),
    )

    # Relationships
    pay_schedule: Mapped[PaySchedule] = relationship(back_populates="periods")


class EmployeePaySchedule(Base, TimestampMixin):
    """Employee assignment to a pay schedule."""

    __tablename__ = "employee_pay_schedule"

    employee_pay_schedule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_schedule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_schedule.pay_schedule_id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="eps_dates_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship()
    pay_schedule: Mapped[PaySchedule] = relationship()


# ===== Pay Rates =====


class PayRate(Base, TimestampMixin):
    """Employee pay rate with dimensional matching."""

    __tablename__ = "pay_rate"

    pay_rate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Dimensional matching
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.job_id"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.project_id"),
        nullable=True,
    )
    department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    worksite_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worksite.worksite_id"),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("currency = 'USD'", name="pay_rate_currency_usd"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="pay_rate_dates_check"),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="pay_rates")
    job: Mapped[Job | None] = relationship()
    project: Mapped[Project | None] = relationship()
    department: Mapped[Department | None] = relationship()
    worksite: Mapped[Worksite | None] = relationship()

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if rate is active on a given date."""
        if self.start_date > as_of_date:
            return False
        if self.end_date is not None and self.end_date < as_of_date:
            return False
        return True

    def matches_dimensions(
        self,
        job_id: UUID | None,
        project_id: UUID | None,
        department_id: UUID | None,
        worksite_id: UUID | None,
    ) -> int:
        """Calculate dimension match score (higher = more specific)."""
        score = 0
        if self.job_id is not None:
            if self.job_id == job_id:
                score += 8
            else:
                return -1  # Explicit mismatch
        if self.project_id is not None:
            if self.project_id == project_id:
                score += 4
            else:
                return -1
        if self.department_id is not None:
            if self.department_id == department_id:
                score += 2
            else:
                return -1
        if self.worksite_id is not None:
            if self.worksite_id == worksite_id:
                score += 1
            else:
                return -1
        return score


# ===== Earnings & Deductions =====


class EarningCode(Base, TimestampMixin):
    """Earning type definition."""

    __tablename__ = "earning_code"

    earning_code_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    earning_category: Mapped[str] = mapped_column(String, nullable=False)
    is_taxable_federal: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_taxable_state_default: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_taxable_local_default: Mapped[bool] = mapped_column(default=True, nullable=False)
    gl_account_hint: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "code", name="earning_code_le_code_unique"),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()


class DeductionCode(Base, TimestampMixin):
    """Deduction type definition."""

    __tablename__ = "deduction_code"

    deduction_code_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    deduction_type: Mapped[str] = mapped_column(String, nullable=False)
    calc_method: Mapped[str] = mapped_column(String, nullable=False)
    limit_type: Mapped[str | None] = mapped_column(String, nullable=True)
    is_employer_match_eligible: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "code", name="deduction_code_le_code_unique"),
        CheckConstraint(
            "deduction_type IN ('pretax', 'posttax', 'roth', 'aftertax', 'loan', 'other')",
            name="deduction_code_type_check",
        ),
        CheckConstraint(
            "calc_method IN ('flat', 'percent', 'tiered')",
            name="deduction_code_method_check",
        ),
        CheckConstraint(
            "limit_type IS NULL OR limit_type IN ('per_check', 'per_period', 'annual')",
            name="deduction_code_limit_check",
        ),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()

    @property
    def is_pretax(self) -> bool:
        """Check if deduction is pre-tax."""
        return self.deduction_type == "pretax"


class EmployeeDeduction(Base, TimestampMixin):
    """Employee enrollment in a deduction."""

    __tablename__ = "employee_deduction"

    employee_deduction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    deduction_code_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deduction_code.deduction_code_id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Employee contribution
    employee_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    employee_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)

    # Employer contribution
    employer_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    employer_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)

    taxability_overrides_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    provider_reference: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="employee_deduction_dates_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="deductions")
    deduction_code: Mapped[DeductionCode] = relationship()

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if deduction is active on a given date."""
        if self.start_date > as_of_date:
            return False
        if self.end_date is not None and self.end_date < as_of_date:
            return False
        return True


class GarnishmentOrder(Base, TimestampMixin):
    """Garnishment order against an employee."""

    __tablename__ = "garnishment_order"

    garnishment_order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    order_type: Mapped[str] = mapped_column(String, nullable=False)
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    case_number: Mapped[str | None] = mapped_column(String, nullable=True)
    payee_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="garnishment_dates_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="garnishments")

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if garnishment is active on a given date."""
        if self.start_date > as_of_date:
            return False
        if self.end_date is not None and self.end_date < as_of_date:
            return False
        return True


# ===== Jurisdictions & Tax Setup =====


class Jurisdiction(Base):
    """Tax jurisdiction (federal/state/local)."""

    __tablename__ = "jurisdiction"

    jurisdiction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    jurisdiction_type: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_jurisdiction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jurisdiction.jurisdiction_id"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("jurisdiction_type", "code", name="jurisdiction_type_code_unique"),
        CheckConstraint(
            "jurisdiction_type IN ('FED', 'STATE', 'LOCAL')",
            name="jurisdiction_type_check",
        ),
    )

    # Relationships
    parent: Mapped[Jurisdiction | None] = relationship(remote_side=[jurisdiction_id])


class TaxAgency(Base, TimestampMixin):
    """Tax agency within a jurisdiction."""

    __tablename__ = "tax_agency"

    tax_agency_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    jurisdiction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jurisdiction.jurisdiction_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    agency_type: Mapped[str] = mapped_column(String, nullable=False)
    registration_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    jurisdiction: Mapped[Jurisdiction] = relationship()


class EmployerTaxAccount(Base, TimestampMixin):
    """Employer registration with a tax agency."""

    __tablename__ = "employer_tax_account"

    employer_tax_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_agency_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tax_agency.tax_agency_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_number_token: Mapped[str | None] = mapped_column(String, nullable=True)
    deposit_schedule: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_start: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="employer_tax_account_dates_check",
        ),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()
    tax_agency: Mapped[TaxAgency] = relationship()


class EmployeeTaxProfile(Base, TimestampMixin):
    """Employee tax withholding profile per jurisdiction."""

    __tablename__ = "employee_tax_profile"

    employee_tax_profile_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    jurisdiction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jurisdiction.jurisdiction_id", ondelete="CASCADE"),
        nullable=False,
    )
    filing_status: Mapped[str | None] = mapped_column(String, nullable=True)
    allowances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    additional_withholding: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    residency_status: Mapped[str | None] = mapped_column(String, nullable=True)
    work_location_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_start: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "residency_status IS NULL OR residency_status IN ('resident', 'nonresident')",
            name="employee_tax_profile_residency_check",
        ),
        CheckConstraint(
            "work_location_basis IS NULL OR work_location_basis IN ('home', 'work', 'both')",
            name="employee_tax_profile_work_location_check",
        ),
        CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="employee_tax_profile_dates_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="tax_profiles")
    jurisdiction: Mapped[Jurisdiction] = relationship()

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if profile is active on a given date."""
        if self.effective_start > as_of_date:
            return False
        if self.effective_end is not None and self.effective_end < as_of_date:
            return False
        return True


# ===== Payroll Rules =====


class PayrollRule(Base, TimestampMixin):
    """Payroll calculation rule definition."""

    __tablename__ = "payroll_rule"

    rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    rule_name: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)


class PayrollRuleVersion(Base, TimestampMixin):
    """Versioned payroll rule with effective dating."""

    __tablename__ = "payroll_rule_version"

    rule_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payroll_rule.rule_id", ondelete="CASCADE"),
        nullable=False,
    )
    effective_start: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    source_last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    logic_hash: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="payroll_rule_version_dates_check",
        ),
    )

    # Relationships
    rule: Mapped[PayrollRule] = relationship()

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if version is active on a given date."""
        if self.effective_start > as_of_date:
            return False
        if self.effective_end is not None and self.effective_end < as_of_date:
            return False
        return True


# ===== Pay Run & Immutable Results =====


class PayRun(Base, TimestampMixin):
    """Payroll run container."""

    __tablename__ = "pay_run"

    pay_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_period_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_period.pay_period_id"),
        nullable=True,
    )
    run_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=True,
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "run_type IN ('regular', 'offcycle', 'bonus', 'manual')",
            name="pay_run_type_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'preview', 'approved', 'committed', 'paid', 'voided')",
            name="pay_run_status_check",
        ),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()
    pay_period: Mapped[PayPeriod | None] = relationship()
    employees: Mapped[list[PayRunEmployee]] = relationship(back_populates="pay_run")

    def get_as_of_date(self) -> date:
        """Get the as-of date for calculations (period_end or explicit)."""
        if self.as_of_date is not None:
            return self.as_of_date
        if self.pay_period is not None:
            return self.pay_period.period_end
        raise ValueError("Pay run has no as_of_date and no pay_period")


class PayRunEmployee(Base, TimestampMixin):
    """Employee inclusion in a pay run."""

    __tablename__ = "pay_run_employee"

    pay_run_employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="included")
    calculation_version: Mapped[str] = mapped_column(String, nullable=False)
    gross: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("pay_run_id", "employee_id", name="pay_run_employee_unique"),
        CheckConstraint(
            "status IN ('included', 'excluded', 'error')",
            name="pay_run_employee_status_check",
        ),
        CheckConstraint("currency = 'USD'", name="pay_run_employee_currency_usd"),
    )

    # Relationships
    pay_run: Mapped[PayRun] = relationship(back_populates="employees")
    employee: Mapped[Employee] = relationship()
    statement: Mapped[PayStatement | None] = relationship(back_populates="pay_run_employee", uselist=False)


class PayStatement(Base, TimestampMixin):
    """Immutable pay statement (one per pay_run_employee)."""

    __tablename__ = "pay_statement"

    pay_statement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_run_employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run_employee.pay_run_employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    check_number: Mapped[str | None] = mapped_column(String, nullable=True)
    check_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String, nullable=False)
    statement_status: Mapped[str] = mapped_column(String, nullable=False, default="issued")
    net_pay: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    calculation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("pay_run_employee_id", name="pay_statement_one_per_pre"),
        CheckConstraint(
            "payment_method IN ('ach', 'check', 'paycard', 'other')",
            name="pay_statement_method_check",
        ),
        CheckConstraint(
            "statement_status IN ('issued', 'voided', 'reissued')",
            name="pay_statement_status_check",
        ),
    )

    # Relationships
    pay_run_employee: Mapped[PayRunEmployee] = relationship(back_populates="statement")
    line_items: Mapped[list[PayLineItem]] = relationship(back_populates="statement")


class PayLineItem(Base, TimestampMixin):
    """Immutable pay line item."""

    __tablename__ = "pay_line_item"

    pay_line_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_statement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_statement.pay_statement_id", ondelete="CASCADE"),
        nullable=False,
    )
    line_type: Mapped[str] = mapped_column(String, nullable=False)
    earning_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("earning_code.earning_code_id"),
        nullable=True,
    )
    deduction_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deduction_code.deduction_code_id"),
        nullable=True,
    )
    tax_agency_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tax_agency.tax_agency_id"),
        nullable=True,
    )
    jurisdiction_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jurisdiction.jurisdiction_id"),
        nullable=True,
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    taxability_flags_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    source_input_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    rule_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payroll_rule.rule_id"),
        nullable=True,
    )
    rule_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payroll_rule_version.rule_version_id"),
        nullable=True,
    )
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Idempotency
    calculation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    line_hash: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "line_type IN ('EARNING', 'DEDUCTION', 'TAX', 'EMPLOYER_TAX', 'REIMBURSEMENT', 'ROUNDING')",
            name="pay_line_item_type_check",
        ),
        CheckConstraint(
            "(line_type NOT IN ('TAX', 'EMPLOYER_TAX')) OR (jurisdiction_id IS NOT NULL)",
            name="pay_line_item_tax_jurisdiction_check",
        ),
    )

    # Relationships
    statement: Mapped[PayStatement] = relationship(back_populates="line_items")
    earning_code: Mapped[EarningCode | None] = relationship()
    deduction_code: Mapped[DeductionCode | None] = relationship()
    tax_agency: Mapped[TaxAgency | None] = relationship()
    jurisdiction: Mapped[Jurisdiction | None] = relationship()
    rule: Mapped[PayrollRule | None] = relationship()
    rule_version: Mapped[PayrollRuleVersion | None] = relationship()


# ===== Pay Run Lock =====


class PayRunLock(Base):
    """Snapshot hash of locked config at approval time."""

    __tablename__ = "pay_run_lock"

    pay_run_lock_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String, nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("pay_run_id", "entity_type", "entity_id", name="pay_run_lock_unique"),
    )


# ===== Inputs =====


class TimeEntry(Base, TimestampMixin):
    """Time/hours input."""

    __tablename__ = "time_entry"

    time_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    earning_code_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("earning_code.earning_code_id", ondelete="CASCADE"),
        nullable=False,
    )
    hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    units: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    rate_override: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.job_id"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.project_id"),
        nullable=True,
    )
    worksite_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worksite.worksite_id"),
        nullable=True,
    )
    source_system: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Locking
    locked_by_pay_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id"),
        nullable=True,
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source_system IN ('manual', 'import', 'api')",
            name="time_entry_source_check",
        ),
        CheckConstraint(
            "(hours IS NOT NULL)::int + (units IS NOT NULL)::int >= 1",
            name="time_entry_hours_or_units_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship(back_populates="time_entries")
    earning_code: Mapped[EarningCode] = relationship()
    department: Mapped[Department | None] = relationship()
    job: Mapped[Job | None] = relationship()
    project: Mapped[Project | None] = relationship()
    worksite: Mapped[Worksite | None] = relationship()


class PayInputAdjustment(Base, TimestampMixin):
    """One-time earning or deduction adjustment."""

    __tablename__ = "pay_input_adjustment"

    pay_input_adjustment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_pay_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_pay_period_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_period.pay_period_id", ondelete="SET NULL"),
        nullable=True,
    )
    adjustment_type: Mapped[str] = mapped_column(String, nullable=False)
    earning_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("earning_code.earning_code_id"),
        nullable=True,
    )
    deduction_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deduction_code.deduction_code_id"),
        nullable=True,
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.job_id"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.project_id"),
        nullable=True,
    )
    worksite_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worksite.worksite_id"),
        nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=True,
    )

    # Locking
    locked_by_pay_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id"),
        nullable=True,
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "adjustment_type IN ('earning', 'deduction')",
            name="pay_input_adjustment_type_check",
        ),
        CheckConstraint(
            "(adjustment_type = 'earning' AND earning_code_id IS NOT NULL AND deduction_code_id IS NULL) OR "
            "(adjustment_type = 'deduction' AND deduction_code_id IS NOT NULL AND earning_code_id IS NULL)",
            name="pay_input_adjustment_code_check",
        ),
        CheckConstraint(
            "amount IS NOT NULL OR (quantity IS NOT NULL AND rate IS NOT NULL)",
            name="pay_input_adjustment_amount_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship()
    earning_code: Mapped[EarningCode | None] = relationship()
    deduction_code: Mapped[DeductionCode | None] = relationship()
    department: Mapped[Department | None] = relationship()
    job: Mapped[Job | None] = relationship()
    project: Mapped[Project | None] = relationship()
    worksite: Mapped[Worksite | None] = relationship()


# ===== Audit =====


class AuditEvent(Base, TimestampMixin):
    """Audit trail entry."""

    __tablename__ = "audit_event"

    audit_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_user.user_id"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
