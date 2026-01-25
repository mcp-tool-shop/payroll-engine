"""Type definitions for calculation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class LineType(str, Enum):
    """Pay line item types."""

    EARNING = "EARNING"
    DEDUCTION = "DEDUCTION"
    TAX = "TAX"
    EMPLOYER_TAX = "EMPLOYER_TAX"
    REIMBURSEMENT = "REIMBURSEMENT"
    ROUNDING = "ROUNDING"


@dataclass
class LineCandidate:
    """A candidate line item before persistence."""

    line_type: LineType
    amount: Decimal  # Final amount (signed per conventions)

    # Identifiers (at most one should be set)
    earning_code_id: UUID | None = None
    deduction_code_id: UUID | None = None
    tax_agency_id: UUID | None = None
    jurisdiction_id: UUID | None = None

    # Quantity/rate (for hourly earnings)
    quantity: Decimal | None = None
    rate: Decimal | None = None

    # Traceability
    source_input_id: UUID | None = None
    rule_id: UUID | None = None
    rule_version_id: UUID | None = None
    explanation: str | None = None

    # Taxability
    taxability_flags: dict[str, Any] = field(default_factory=dict)

    def to_canonical_dict(self) -> dict[str, Any]:
        """Return canonical dict for hashing (deterministic ordering)."""
        return {
            "line_type": self.line_type.value,
            "earning_code_id": str(self.earning_code_id) if self.earning_code_id else None,
            "deduction_code_id": str(self.deduction_code_id) if self.deduction_code_id else None,
            "tax_agency_id": str(self.tax_agency_id) if self.tax_agency_id else None,
            "jurisdiction_id": str(self.jurisdiction_id) if self.jurisdiction_id else None,
            "source_input_id": str(self.source_input_id) if self.source_input_id else None,
            "rule_id": str(self.rule_id) if self.rule_id else None,
            "rule_version_id": str(self.rule_version_id) if self.rule_version_id else None,
            "quantity": str(self.quantity) if self.quantity else None,
            "rate": str(self.rate) if self.rate else None,
            "amount": str(self.amount),
        }


@dataclass
class TaxableWages:
    """Taxable wages by jurisdiction type."""

    federal: Decimal = Decimal("0")
    state: dict[str, Decimal] = field(default_factory=dict)  # state_code -> amount
    local: dict[str, Decimal] = field(default_factory=dict)  # local_code -> amount

    # Social Security / Medicare
    social_security: Decimal = Decimal("0")
    medicare: Decimal = Decimal("0")


@dataclass
class EmployeeCalculationContext:
    """Context for calculating a single employee's pay."""

    employee_id: UUID
    pay_run_id: UUID
    as_of_date: Any  # date
    check_date: Any  # date
    period_start: Any  # date
    period_end: Any  # date
    legal_entity_id: UUID

    # Will be populated during calculation
    gross: Decimal = Decimal("0")
    net: Decimal = Decimal("0")
    taxable_wages: TaxableWages = field(default_factory=TaxableWages)
    lines: list[LineCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


@dataclass
class TaxBracket:
    """Tax bracket for progressive taxation."""

    min_amount: Decimal
    max_amount: Decimal | None  # None = no upper limit
    rate: Decimal  # As decimal, e.g., 0.22 for 22%
    flat_amount: Decimal = Decimal("0")  # Flat amount at bracket start


@dataclass
class TaxRule:
    """Tax rule configuration."""

    rule_id: UUID
    rule_version_id: UUID
    jurisdiction_id: UUID
    tax_agency_id: UUID | None
    tax_type: str  # 'income', 'social_security', 'medicare', 'suta', 'futa', etc.
    brackets: list[TaxBracket]
    wage_base_limit: Decimal | None = None  # For SS, FUTA, SUTA
    is_employer_tax: bool = False
