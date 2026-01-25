"""Tests for line item builder."""

from decimal import Decimal
from uuid import uuid4

import pytest

from payroll_engine.calculators.line_builder import LineItemBuilder
from payroll_engine.calculators.types import LineCandidate, LineType


class TestLineItemBuilder:
    """Test line item builder functionality."""

    def test_round_to_cents(self):
        """Test rounding to 2 decimal places."""
        # Standard rounding
        assert LineItemBuilder.round_to_cents(Decimal("10.125")) == Decimal("10.13")
        assert LineItemBuilder.round_to_cents(Decimal("10.124")) == Decimal("10.12")

        # Half-up rounding
        assert LineItemBuilder.round_to_cents(Decimal("10.125")) == Decimal("10.13")
        assert LineItemBuilder.round_to_cents(Decimal("10.135")) == Decimal("10.14")

    def test_create_earning_line(self):
        """Test creating earning line (positive amount)."""
        earning_code_id = uuid4()
        line = LineItemBuilder.create_earning_line(
            earning_code_id=earning_code_id,
            amount=Decimal("1000.00"),
            quantity=Decimal("40.00"),
            rate=Decimal("25.00"),
            explanation="Regular hours",
        )

        assert line.line_type == LineType.EARNING
        assert line.amount == Decimal("1000.00")
        assert line.amount > 0  # Earnings are positive
        assert line.earning_code_id == earning_code_id
        assert line.quantity == Decimal("40.00")
        assert line.rate == Decimal("25.00")

    def test_create_deduction_line(self):
        """Test creating deduction line (negative amount)."""
        deduction_code_id = uuid4()
        line = LineItemBuilder.create_deduction_line(
            deduction_code_id=deduction_code_id,
            amount=Decimal("100.00"),
            explanation="401k contribution",
        )

        assert line.line_type == LineType.DEDUCTION
        assert line.amount == Decimal("-100.00")
        assert line.amount < 0  # Deductions are negative
        assert line.deduction_code_id == deduction_code_id

    def test_create_tax_line(self):
        """Test creating employee tax line (negative amount)."""
        jurisdiction_id = uuid4()
        rule_id = uuid4()
        rule_version_id = uuid4()

        line = LineItemBuilder.create_tax_line(
            jurisdiction_id=jurisdiction_id,
            amount=Decimal("150.00"),
            rule_id=rule_id,
            rule_version_id=rule_version_id,
            explanation="Federal income tax",
        )

        assert line.line_type == LineType.TAX
        assert line.amount == Decimal("-150.00")
        assert line.amount < 0  # Employee taxes are negative
        assert line.jurisdiction_id == jurisdiction_id
        assert line.rule_id == rule_id
        assert line.rule_version_id == rule_version_id

    def test_create_employer_tax_line(self):
        """Test creating employer tax line (positive amount - liability)."""
        jurisdiction_id = uuid4()
        rule_id = uuid4()
        rule_version_id = uuid4()

        line = LineItemBuilder.create_employer_tax_line(
            jurisdiction_id=jurisdiction_id,
            amount=Decimal("62.00"),
            rule_id=rule_id,
            rule_version_id=rule_version_id,
            explanation="Social Security (employer)",
        )

        assert line.line_type == LineType.EMPLOYER_TAX
        assert line.amount == Decimal("62.00")
        assert line.amount > 0  # Employer taxes are positive (liability)

    def test_create_rounding_line(self):
        """Test creating rounding adjustment line."""
        # Positive adjustment
        line_pos = LineItemBuilder.create_rounding_line(Decimal("0.01"))
        assert line_pos.line_type == LineType.ROUNDING
        assert line_pos.amount == Decimal("0.01")

        # Negative adjustment
        line_neg = LineItemBuilder.create_rounding_line(Decimal("-0.02"))
        assert line_neg.line_type == LineType.ROUNDING
        assert line_neg.amount == Decimal("-0.02")

    def test_calculate_net_from_lines(self):
        """Test net calculation from line items."""
        lines = [
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("1000.00")),
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("-100.00")),
            LineCandidate(line_type=LineType.TAX, amount=Decimal("-150.00")),
            LineCandidate(line_type=LineType.EMPLOYER_TAX, amount=Decimal("62.00")),  # Not in net
        ]

        net = LineItemBuilder.calculate_net_from_lines(lines)

        # Net = 1000 - 100 - 150 = 750 (employer tax excluded)
        assert net == Decimal("750.00")

    def test_calculate_gross_from_lines(self):
        """Test gross calculation from line items."""
        lines = [
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("1000.00")),
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("200.00")),
            LineCandidate(line_type=LineType.REIMBURSEMENT, amount=Decimal("50.00")),
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("-100.00")),
        ]

        gross = LineItemBuilder.calculate_gross_from_lines(lines)

        # Gross = 1000 + 200 + 50 = 1250
        assert gross == Decimal("1250.00")

    def test_validate_line_signs(self):
        """Test sign validation for line items."""
        # Valid signs
        valid_lines = [
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("1000.00")),
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("-100.00")),
            LineCandidate(line_type=LineType.TAX, amount=Decimal("-150.00")),
            LineCandidate(line_type=LineType.EMPLOYER_TAX, amount=Decimal("62.00")),
        ]
        errors = LineItemBuilder.validate_line_signs(valid_lines)
        assert errors == []

        # Invalid signs
        invalid_lines = [
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("-1000.00")),  # Wrong
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("100.00")),  # Wrong
        ]
        errors = LineItemBuilder.validate_line_signs(invalid_lines)
        assert len(errors) == 2

    def test_compute_line_hash_deterministic(self):
        """Test that line hash is deterministic."""
        earning_code_id = uuid4()
        source_id = uuid4()

        line1 = LineCandidate(
            line_type=LineType.EARNING,
            amount=Decimal("1000.00"),
            earning_code_id=earning_code_id,
            quantity=Decimal("40"),
            rate=Decimal("25"),
            source_input_id=source_id,
        )

        line2 = LineCandidate(
            line_type=LineType.EARNING,
            amount=Decimal("1000.00"),
            earning_code_id=earning_code_id,
            quantity=Decimal("40"),
            rate=Decimal("25"),
            source_input_id=source_id,
        )

        hash1 = LineItemBuilder.compute_line_hash(line1)
        hash2 = LineItemBuilder.compute_line_hash(line2)

        assert hash1 == hash2

    def test_compute_line_hash_different_for_different_data(self):
        """Test that different data produces different hashes."""
        earning_code_id = uuid4()

        line1 = LineCandidate(
            line_type=LineType.EARNING,
            amount=Decimal("1000.00"),
            earning_code_id=earning_code_id,
        )

        line2 = LineCandidate(
            line_type=LineType.EARNING,
            amount=Decimal("1001.00"),  # Different amount
            earning_code_id=earning_code_id,
        )

        hash1 = LineItemBuilder.compute_line_hash(line1)
        hash2 = LineItemBuilder.compute_line_hash(line2)

        assert hash1 != hash2

    def test_sum_by_type(self):
        """Test summing lines by type."""
        lines = [
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("800.00")),
            LineCandidate(line_type=LineType.EARNING, amount=Decimal("200.00")),
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("-50.00")),
            LineCandidate(line_type=LineType.DEDUCTION, amount=Decimal("-30.00")),
            LineCandidate(line_type=LineType.TAX, amount=Decimal("-100.00")),
        ]

        totals = LineItemBuilder.sum_by_type(lines)

        assert totals[LineType.EARNING] == Decimal("1000.00")
        assert totals[LineType.DEDUCTION] == Decimal("-80.00")
        assert totals[LineType.TAX] == Decimal("-100.00")
        assert totals[LineType.EMPLOYER_TAX] == Decimal("0")
