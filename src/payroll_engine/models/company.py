"""Company and organizational structure models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from payroll_engine.models.employee import Employee


class Tenant(Base, TimestampMixin):
    """Multi-tenant container."""

    __tablename__ = "tenant"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="active",
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'closed')", name="tenant_status_check"),
    )

    # Relationships
    legal_entities: Mapped[list[LegalEntity]] = relationship(back_populates="tenant")
    employees: Mapped[list[Employee]] = relationship(back_populates="tenant")


class Address(Base, TimestampMixin):
    """Physical address."""

    __tablename__ = "address"

    address_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    line1: Mapped[str] = mapped_column(String, nullable=False)
    line2: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    postal_code: Mapped[str] = mapped_column(String, nullable=False)
    county: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, nullable=False, default="US")

    __table_args__ = (CheckConstraint("country = 'US'", name="address_country_us"),)


class LegalEntity(Base, TimestampMixin):
    """Legal entity (employer) within a tenant."""

    __tablename__ = "legal_entity"

    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    legal_name: Mapped[str] = mapped_column(String, nullable=False)
    dba_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ein: Mapped[str] = mapped_column(String, nullable=False)
    address_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("address.address_id"),
        nullable=True,
    )

    __table_args__ = (UniqueConstraint("tenant_id", "ein", name="legal_entity_tenant_ein_unique"),)

    # Relationships
    tenant: Mapped[Tenant] = relationship(back_populates="legal_entities")
    address: Mapped[Address | None] = relationship()
    worksites: Mapped[list[Worksite]] = relationship(back_populates="legal_entity")
    departments: Mapped[list[Department]] = relationship(back_populates="legal_entity")
    jobs: Mapped[list[Job]] = relationship(back_populates="legal_entity")
    projects: Mapped[list[Project]] = relationship(back_populates="legal_entity")


class Worksite(Base, TimestampMixin):
    """Physical work location."""

    __tablename__ = "worksite"

    worksite_id: Mapped[UUID] = mapped_column(
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
    address_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("address.address_id"),
        nullable=True,
    )
    worksite_code: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "worksite_code", name="worksite_le_code_unique"),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship(back_populates="worksites")
    address: Mapped[Address | None] = relationship()


class Department(Base):
    """Department within a legal entity."""

    __tablename__ = "department"

    department_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    department_code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "department_code", name="department_le_code_unique"),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship(back_populates="departments")


class Job(Base):
    """Job/position definition."""

    __tablename__ = "job"

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    job_code: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    is_union_eligible: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "job_code", name="job_le_code_unique"),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship(back_populates="jobs")


class Project(Base):
    """Project for cost allocation."""

    __tablename__ = "project"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    project_code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "project_code", name="project_le_code_unique"),
        CheckConstraint(
            "status IN ('active', 'closed', 'archived')",
            name="project_status_check",
        ),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship(back_populates="projects")
