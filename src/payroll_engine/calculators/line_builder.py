"""Line item builder with idempotent hashing."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from payroll_engine.calculators.types import LineCandidate, LineType

if TYPE_CHECKING:
    pass


class LineItemBuilder:
    """Builds line items with deterministic hashing for idempotency.

    Sign conventions (non-negotiable):
    - EARNING: positive
    - REIMBURSEMENT: positive
    - DEDUCTION (employee): negative
    - TAX (employee): negative
    - EMPLOYER_TAX: positive (liability)
    - ROUNDING: can be positive or negative

    Rounding:
    - USD to 2 decimals at persistence
    - Internal compute at >=4 decimals
    - Explicit rounding line if penny drift exists
    """

    PRECISION = Decimal("0.0001")  # 4 decimal places for internal calculations
    OUTPUT_PRECISION = Decimal("0.01")  # 2 decimal places for persistence

    @staticmethod
    def round_to_cents(amount: Decimal) -> Decimal:
        """Round amount to 2 decimal places (cents)."""
        return amount.quantize(LineItemBuilder.OUTPUT_PRECISION, rounding=ROUND_HALF_UP)

    @staticmethod
    def compute_line_hash(line: LineCandidate) -> str:
        """Compute deterministic hash for a line item.

        The hash is based on the canonical representation of defining fields,
        ensuring identical inputs produce identical hashes.
        """
        canonical = line.to_canonical_dict()
        json_str = json.dumps(canonical, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]

    @staticmethod
    def create_earning_line(
        earning_code_id: UUID,
        amount: Decimal,
        quantity: Decimal | None = None,
        rate: Decimal | None = None,
        source_input_id: UUID | None = None,
        explanation: str | None = None,
        taxability_flags: dict[str, Any] | None = None,
    ) -> LineCandidate:
        """Create an earning line item (positive amount)."""
        return LineCandidate(
            line_type=LineType.EARNING,
            amount=LineItemBuilder.round_to_cents(abs(amount)),  # Ensure positive
            earning_code_id=earning_code_id,
            quantity=quantity,
            rate=rate,
            source_input_id=source_input_id,
            explanation=explanation,
            taxability_flags=taxability_flags or {},
        )

    @staticmethod
    def create_deduction_line(
        deduction_code_id: UUID,
        amount: Decimal,
        rule_id: UUID | None = None,
        rule_version_id: UUID | None = None,
        explanation: str | None = None,
    ) -> LineCandidate:
        """Create a deduction line item (negative amount)."""
        return LineCandidate(
            line_type=LineType.DEDUCTION,
            amount=-LineItemBuilder.round_to_cents(abs(amount)),  # Ensure negative
            deduction_code_id=deduction_code_id,
            rule_id=rule_id,
            rule_version_id=rule_version_id,
            explanation=explanation,
        )

    @staticmethod
    def create_tax_line(
        jurisdiction_id: UUID,
        amount: Decimal,
        rule_id: UUID,
        rule_version_id: UUID,
        tax_agency_id: UUID | None = None,
        explanation: str | None = None,
    ) -> LineCandidate:
        """Create an employee tax line item (negative amount)."""
        return LineCandidate(
            line_type=LineType.TAX,
            amount=-LineItemBuilder.round_to_cents(abs(amount)),  # Ensure negative
            jurisdiction_id=jurisdiction_id,
            tax_agency_id=tax_agency_id,
            rule_id=rule_id,
            rule_version_id=rule_version_id,
            explanation=explanation,
        )

    @staticmethod
    def create_employer_tax_line(
        jurisdiction_id: UUID,
        amount: Decimal,
        rule_id: UUID,
        rule_version_id: UUID,
        tax_agency_id: UUID | None = None,
        explanation: str | None = None,
    ) -> LineCandidate:
        """Create an employer tax line item (positive amount, liability)."""
        return LineCandidate(
            line_type=LineType.EMPLOYER_TAX,
            amount=LineItemBuilder.round_to_cents(abs(amount)),  # Ensure positive
            jurisdiction_id=jurisdiction_id,
            tax_agency_id=tax_agency_id,
            rule_id=rule_id,
            rule_version_id=rule_version_id,
            explanation=explanation,
        )

    @staticmethod
    def create_reimbursement_line(
        earning_code_id: UUID,
        amount: Decimal,
        source_input_id: UUID | None = None,
        explanation: str | None = None,
    ) -> LineCandidate:
        """Create a reimbursement line item (positive amount)."""
        return LineCandidate(
            line_type=LineType.REIMBURSEMENT,
            amount=LineItemBuilder.round_to_cents(abs(amount)),  # Ensure positive
            earning_code_id=earning_code_id,
            source_input_id=source_input_id,
            explanation=explanation,
        )

    @staticmethod
    def create_rounding_line(amount: Decimal) -> LineCandidate:
        """Create a rounding adjustment line item.

        Amount can be positive or negative to reconcile penny drift.
        """
        return LineCandidate(
            line_type=LineType.ROUNDING,
            amount=LineItemBuilder.round_to_cents(amount),
            explanation="Rounding adjustment",
        )

    @staticmethod
    def calculate_net_from_lines(lines: list[LineCandidate]) -> Decimal:
        """Calculate net pay from line items.

        NET = Σ(EARNING) + Σ(REIMBURSEMENT) + Σ(DEDUCTION) + Σ(TAX) + Σ(ROUNDING)

        Note: EMPLOYER_TAX is excluded from net calculation (it's a liability).
        """
        net = Decimal("0")
        for line in lines:
            if line.line_type != LineType.EMPLOYER_TAX:
                net += line.amount
        return LineItemBuilder.round_to_cents(net)

    @staticmethod
    def calculate_gross_from_lines(lines: list[LineCandidate]) -> Decimal:
        """Calculate gross pay from line items.

        GROSS = Σ(EARNING) + Σ(REIMBURSEMENT)
        """
        gross = Decimal("0")
        for line in lines:
            if line.line_type in (LineType.EARNING, LineType.REIMBURSEMENT):
                gross += line.amount
        return LineItemBuilder.round_to_cents(gross)

    @staticmethod
    def reconcile_rounding(
        lines: list[LineCandidate], expected_net: Decimal
    ) -> list[LineCandidate]:
        """Add rounding adjustment line if needed to reconcile net.

        Compares calculated net to expected net and adds adjustment line
        if there's penny drift. Does not modify existing lines.
        """
        calculated_net = LineItemBuilder.calculate_net_from_lines(lines)
        diff = expected_net - calculated_net

        if diff == 0:
            return lines

        # Create rounding adjustment
        rounding_line = LineItemBuilder.create_rounding_line(diff)
        return lines + [rounding_line]

    @staticmethod
    def validate_line_signs(lines: list[LineCandidate]) -> list[str]:
        """Validate that all line items have correct signs.

        Returns list of error messages (empty if all valid).
        """
        errors: list[str] = []

        for i, line in enumerate(lines):
            if line.line_type in (LineType.EARNING, LineType.REIMBURSEMENT, LineType.EMPLOYER_TAX):
                if line.amount < 0:
                    errors.append(
                        f"Line {i} ({line.line_type.value}) has negative amount {line.amount}, expected positive"
                    )
            elif line.line_type in (LineType.DEDUCTION, LineType.TAX):
                if line.amount > 0:
                    errors.append(
                        f"Line {i} ({line.line_type.value}) has positive amount {line.amount}, expected negative"
                    )
            # ROUNDING can be either sign

        return errors

    @staticmethod
    def sum_by_type(lines: list[LineCandidate]) -> dict[LineType, Decimal]:
        """Sum line amounts by type."""
        totals: dict[LineType, Decimal] = {lt: Decimal("0") for lt in LineType}
        for line in lines:
            totals[line.line_type] += line.amount
        return totals
