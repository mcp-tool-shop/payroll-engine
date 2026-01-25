"""Unit tests for TaxCalculator.

Tests the tax calculation logic with mocked dependencies.
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from payroll_engine.calculators.tax_calculator import TaxCalculator
from payroll_engine.calculators.types import (
    TaxBracket,
    TaxRule,
    TaxableWages,
    LineType,
)


class TestProgressiveTaxCalculation:
    """Test progressive tax bracket calculations."""

    def test_single_bracket_calculation(self):
        """Single bracket applies to full wages."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        # Create calculator without session (testing static methods)
        calc = TaxCalculator.__new__(TaxCalculator)

        result = calc._calculate_progressive_tax(Decimal("1000"), rule)
        assert result == Decimal("100.00")

    def test_multiple_bracket_calculation(self):
        """Multiple brackets with progressive rates."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="federal_income",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=Decimal("10000"),
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("0"),
                ),
                TaxBracket(
                    min_amount=Decimal("10000"),
                    max_amount=Decimal("40000"),
                    rate=Decimal("0.12"),
                    flat_amount=Decimal("0"),
                ),
                TaxBracket(
                    min_amount=Decimal("40000"),
                    max_amount=None,
                    rate=Decimal("0.22"),
                    flat_amount=Decimal("0"),
                ),
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # Test within first bracket
        result = calc._calculate_progressive_tax(Decimal("5000"), rule)
        assert result == Decimal("500.00")  # 5000 * 0.10

        # Test spanning first and second bracket
        result = calc._calculate_progressive_tax(Decimal("15000"), rule)
        # 10000 * 0.10 = 1000
        # 5000 * 0.12 = 600
        # Total = 1600
        assert result == Decimal("1600.00")

        # Test spanning all brackets
        result = calc._calculate_progressive_tax(Decimal("50000"), rule)
        # 10000 * 0.10 = 1000
        # 30000 * 0.12 = 3600
        # 10000 * 0.22 = 2200
        # Total = 6800
        assert result == Decimal("6800.00")

    def test_zero_wages_returns_zero(self):
        """Zero wages should return zero tax."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_progressive_tax(Decimal("0"), rule)
        assert result == Decimal("0")

    def test_negative_wages_returns_zero(self):
        """Negative wages should return zero tax."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_progressive_tax(Decimal("-1000"), rule)
        assert result == Decimal("0")

    def test_additional_withholding_added(self):
        """Additional withholding should be added to tax."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_progressive_tax(
            Decimal("1000"),
            rule,
            additional_withholding=Decimal("50"),
        )
        assert result == Decimal("150.00")  # 100 + 50

    def test_flat_amount_in_bracket(self):
        """Bracket with flat amount plus rate."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=Decimal("10000"),
                    rate=Decimal("0.10"),
                    flat_amount=Decimal("100"),
                ),
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_progressive_tax(Decimal("5000"), rule)
        # flat 100 + (5000 * 0.10) = 100 + 500 = 600
        assert result == Decimal("600.00")


class TestWageBaseTaxCalculation:
    """Test wage base limited tax calculations (SS, FUTA, etc.)."""

    def test_under_wage_base(self):
        """Wages under the wage base are fully taxed."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="social_security",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.062"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=Decimal("160200"),
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_wage_base_tax(Decimal("5000"), rule, Decimal("0"))
        assert result == Decimal("310.00")  # 5000 * 0.062

    def test_reaching_wage_base(self):
        """Only wages up to remaining base are taxed."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="social_security",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.062"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=Decimal("160200"),
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # YTD = 160000, only 200 remaining under base
        result = calc._calculate_wage_base_tax(
            Decimal("5000"), rule, Decimal("160000")
        )
        assert result == Decimal("12.40")  # 200 * 0.062

    def test_over_wage_base(self):
        """Wages when already over wage base return zero."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="social_security",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.062"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=Decimal("160200"),
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # YTD already exceeds wage base
        result = calc._calculate_wage_base_tax(
            Decimal("5000"), rule, Decimal("170000")
        )
        assert result == Decimal("0")

    def test_no_wage_base_limit(self):
        """Without wage base limit, all wages are taxed."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="medicare",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.0145"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_wage_base_tax(Decimal("1000000"), rule, Decimal("500000"))
        # No limit, so full wages are taxed
        assert result == Decimal("14500.00")


class TestFlatTaxCalculation:
    """Test flat rate tax calculations."""

    def test_flat_tax_calculation(self):
        """Flat tax applies rate to full wages."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="local",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.02"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_flat_tax(Decimal("5000"), rule)
        assert result == Decimal("100.00")  # 5000 * 0.02

    def test_flat_tax_zero_wages(self):
        """Zero wages return zero flat tax."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="local",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.02"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_flat_tax(Decimal("0"), rule)
        assert result == Decimal("0")

    def test_flat_tax_no_brackets(self):
        """No brackets returns zero tax."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="local",
            brackets=[],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)
        result = calc._calculate_flat_tax(Decimal("5000"), rule)
        assert result == Decimal("0")


class TestTaxableWagesDataclass:
    """Test TaxableWages dataclass behavior."""

    def test_default_values(self):
        """TaxableWages has correct defaults."""
        wages = TaxableWages(federal=Decimal("1000"))

        assert wages.federal == Decimal("1000")
        assert wages.social_security == Decimal("0")
        assert wages.medicare == Decimal("0")
        assert wages.state == {}
        assert wages.local == {}

    def test_state_wages(self):
        """Can set state-specific wages."""
        wages = TaxableWages(
            federal=Decimal("5000"),
            state={"CA": Decimal("5000"), "NY": Decimal("2000")},
        )

        assert wages.state["CA"] == Decimal("5000")
        assert wages.state["NY"] == Decimal("2000")

    def test_local_wages(self):
        """Can set local-specific wages."""
        wages = TaxableWages(
            federal=Decimal("5000"),
            local={"NYC": Decimal("2000")},
        )

        assert wages.local["NYC"] == Decimal("2000")


class TestDecimalPrecision:
    """Test that calculations maintain proper decimal precision."""

    def test_rounding_to_cents(self):
        """Tax amounts should be rounded to cents."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.1234"),  # Rate that causes rounding
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # 1000 * 0.1234 = 123.4 -> should round to 123.40
        result = calc._calculate_progressive_tax(Decimal("1000"), rule)
        assert result == Decimal("123.40")
        assert str(result) == "123.40"

    def test_half_up_rounding(self):
        """Uses ROUND_HALF_UP rounding mode."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.0333"),  # Creates .5 cent situation
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # 100 * 0.0333 = 3.33
        result = calc._calculate_progressive_tax(Decimal("100"), rule)
        assert result == Decimal("3.33")

        # 15 * 0.0333 = 0.4995 -> rounds to 0.50
        result = calc._calculate_progressive_tax(Decimal("15"), rule)
        assert result == Decimal("0.50")

    def test_no_floating_point_contamination(self):
        """Results are true Decimal, not float-derived."""
        rule = TaxRule(
            rule_id=uuid4(),
            rule_version_id=uuid4(),
            jurisdiction_id=uuid4(),
            tax_agency_id=None,
            tax_type="test",
            brackets=[
                TaxBracket(
                    min_amount=Decimal("0"),
                    max_amount=None,
                    rate=Decimal("0.1"),
                    flat_amount=Decimal("0"),
                )
            ],
            wage_base_limit=None,
            is_employer_tax=False,
        )

        calc = TaxCalculator.__new__(TaxCalculator)

        # Classic float problem: 0.1 + 0.2 != 0.3 in float
        # But with Decimal it should work correctly
        result1 = calc._calculate_progressive_tax(Decimal("1"), rule)
        result2 = calc._calculate_progressive_tax(Decimal("2"), rule)
        result3 = calc._calculate_progressive_tax(Decimal("3"), rule)

        assert result1 + result2 == result3
        assert result1 == Decimal("0.10")
        assert result2 == Decimal("0.20")
        assert result3 == Decimal("0.30")
