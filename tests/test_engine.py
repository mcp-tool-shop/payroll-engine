"""Unit tests for PayrollEngine.

Tests the engine logic with mocked dependencies.
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from payroll_engine.calculators.engine import (
    PayrollEngine,
    CalculationResult,
    PayRunCalculationResult,
)
from payroll_engine.calculators.types import (
    EmployeeCalculationContext,
    LineCandidate,
    LineType,
    TaxableWages,
)


class TestCalculationIdGeneration:
    """Test deterministic calculation ID generation."""

    def test_same_inputs_produce_same_id(self):
        """Same inputs should always produce the same calculation ID."""
        engine = PayrollEngine.__new__(PayrollEngine)
        engine.settings = type("Settings", (), {"engine_version": "1.0.0"})()

        pay_run_id = uuid4()
        employee_id = uuid4()
        as_of_date = date(2026, 1, 15)
        inputs_fp = "abc123"
        rules_fp = "def456"

        id1 = engine._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, inputs_fp, rules_fp
        )
        id2 = engine._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, inputs_fp, rules_fp
        )

        assert id1 == id2

    def test_different_inputs_produce_different_id(self):
        """Different inputs should produce different calculation IDs."""
        engine = PayrollEngine.__new__(PayrollEngine)
        engine.settings = type("Settings", (), {"engine_version": "1.0.0"})()

        pay_run_id = uuid4()
        employee_id = uuid4()
        as_of_date = date(2026, 1, 15)

        id1 = engine._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, "abc123", "def456"
        )
        id2 = engine._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, "xyz789", "def456"
        )

        assert id1 != id2

    def test_engine_version_affects_id(self):
        """Different engine versions should produce different IDs."""
        pay_run_id = uuid4()
        employee_id = uuid4()
        as_of_date = date(2026, 1, 15)

        engine1 = PayrollEngine.__new__(PayrollEngine)
        engine1.settings = type("Settings", (), {"engine_version": "1.0.0"})()

        engine2 = PayrollEngine.__new__(PayrollEngine)
        engine2.settings = type("Settings", (), {"engine_version": "2.0.0"})()

        id1 = engine1._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, "abc", "def"
        )
        id2 = engine2._generate_calculation_id(
            pay_run_id, employee_id, as_of_date, "abc", "def"
        )

        assert id1 != id2


class TestFingerprintGeneration:
    """Test fingerprint generation for inputs and rules."""

    def test_inputs_fingerprint_is_deterministic(self):
        """Same inputs should produce same fingerprint."""
        engine = PayrollEngine.__new__(PayrollEngine)

        inputs = [
            {"type": "time_entry", "id": "123", "amount": "100.00"},
            {"type": "adjustment", "id": "456", "amount": "50.00"},
        ]

        fp1 = engine._compute_inputs_fingerprint(inputs)
        fp2 = engine._compute_inputs_fingerprint(inputs)

        assert fp1 == fp2
        assert len(fp1) == 32  # SHA256 truncated to 32 hex chars

    def test_inputs_fingerprint_order_independent(self):
        """Fingerprint should be independent of input order (via sort_keys)."""
        engine = PayrollEngine.__new__(PayrollEngine)

        inputs1 = [
            {"type": "time_entry", "id": "123", "amount": "100.00"},
        ]
        inputs2 = [
            {"amount": "100.00", "id": "123", "type": "time_entry"},
        ]

        fp1 = engine._compute_inputs_fingerprint(inputs1)
        fp2 = engine._compute_inputs_fingerprint(inputs2)

        assert fp1 == fp2

    def test_rules_fingerprint_is_deterministic(self):
        """Same rules should produce same fingerprint."""
        engine = PayrollEngine.__new__(PayrollEngine)

        rules = ["rule1", "rule2", "rule3"]

        fp1 = engine._compute_rules_fingerprint(rules)
        fp2 = engine._compute_rules_fingerprint(rules)

        assert fp1 == fp2

    def test_rules_fingerprint_order_independent(self):
        """Rules fingerprint should be order-independent via sorting."""
        engine = PayrollEngine.__new__(PayrollEngine)

        rules1 = ["rule3", "rule1", "rule2"]
        rules2 = ["rule1", "rule2", "rule3"]

        fp1 = engine._compute_rules_fingerprint(rules1)
        fp2 = engine._compute_rules_fingerprint(rules2)

        assert fp1 == fp2


class TestTaxableWagesCalculation:
    """Test taxable wages calculation logic."""

    def test_basic_taxable_wages(self):
        """Taxable wages = gross - pretax deductions."""
        engine = PayrollEngine.__new__(PayrollEngine)

        gross = Decimal("5000")
        pretax = Decimal("500")  # 401k, HSA, etc.
        earnings_lines = []  # Not used in current implementation

        result = engine._calculate_taxable_wages(gross, pretax, earnings_lines)

        assert result.federal == Decimal("4500")
        assert result.social_security == Decimal("4500")
        assert result.medicare == Decimal("4500")

    def test_zero_pretax_deductions(self):
        """With no pretax deductions, taxable = gross."""
        engine = PayrollEngine.__new__(PayrollEngine)

        gross = Decimal("5000")
        pretax = Decimal("0")

        result = engine._calculate_taxable_wages(gross, pretax, [])

        assert result.federal == gross
        assert result.social_security == gross
        assert result.medicare == gross

    def test_high_pretax_deductions(self):
        """Large pretax deductions reduce taxable wages significantly."""
        engine = PayrollEngine.__new__(PayrollEngine)

        gross = Decimal("5000")
        pretax = Decimal("2000")  # 40% pretax

        result = engine._calculate_taxable_wages(gross, pretax, [])

        assert result.federal == Decimal("3000")


class TestDisposableIncomeCalculation:
    """Test disposable income calculation for garnishments."""

    def test_disposable_income_excludes_garnishments(self):
        """Disposable income should exclude existing garnishment lines."""
        engine = PayrollEngine.__new__(PayrollEngine)

        lines = [
            LineCandidate(
                line_type=LineType.EARNING,
                amount=Decimal("5000"),
                explanation="Regular Pay",
            ),
            LineCandidate(
                line_type=LineType.DEDUCTION,
                amount=Decimal("-500"),
                explanation="401k",
            ),
            LineCandidate(
                line_type=LineType.TAX,
                amount=Decimal("-800"),
                explanation="Federal Tax",
            ),
            LineCandidate(
                line_type=LineType.DEDUCTION,
                amount=Decimal("-200"),
                explanation="Garnishment: Child Support",
            ),
        ]

        # Net = 5000 - 500 - 800 - 200 = 3500
        # But disposable should exclude garnishment = 5000 - 500 - 800 = 3700
        disposable = engine._calculate_disposable_income(lines)

        assert disposable == Decimal("3700")

    def test_disposable_income_with_no_garnishments(self):
        """Without garnishments, disposable equals net."""
        engine = PayrollEngine.__new__(PayrollEngine)

        lines = [
            LineCandidate(
                line_type=LineType.EARNING,
                amount=Decimal("5000"),
            ),
            LineCandidate(
                line_type=LineType.TAX,
                amount=Decimal("-1000"),
            ),
        ]

        disposable = engine._calculate_disposable_income(lines)
        assert disposable == Decimal("4000")


class TestCalculationResultDataclass:
    """Test CalculationResult behavior."""

    def test_success_when_no_errors(self):
        """Result should be success when no errors."""
        result = CalculationResult(
            employee_id=uuid4(),
            calculation_id=uuid4(),
            gross=Decimal("5000"),
            net=Decimal("4000"),
            lines=[],
            errors=[],
            inputs_fingerprint="abc",
            rules_fingerprint="def",
        )

        assert result.success is True

    def test_not_success_when_errors(self):
        """Result should not be success when errors exist."""
        result = CalculationResult(
            employee_id=uuid4(),
            calculation_id=uuid4(),
            gross=Decimal("0"),
            net=Decimal("0"),
            lines=[],
            errors=["Some error occurred"],
            inputs_fingerprint="abc",
            rules_fingerprint="def",
        )

        assert result.success is False

    def test_multiple_errors(self):
        """Can have multiple errors."""
        result = CalculationResult(
            employee_id=uuid4(),
            calculation_id=uuid4(),
            gross=Decimal("0"),
            net=Decimal("0"),
            lines=[],
            errors=["Error 1", "Error 2", "Error 3"],
            inputs_fingerprint="",
            rules_fingerprint="",
        )

        assert result.success is False
        assert len(result.errors) == 3


class TestPayRunCalculationResultDataclass:
    """Test PayRunCalculationResult behavior."""

    def test_aggregates_totals(self):
        """Result should aggregate gross and net totals."""
        result = PayRunCalculationResult(
            pay_run_id=uuid4(),
            results={},
            total_gross=Decimal("10000"),
            total_net=Decimal("8000"),
            error_count=0,
        )

        assert result.total_gross == Decimal("10000")
        assert result.total_net == Decimal("8000")

    def test_tracks_error_count(self):
        """Result should track number of employees with errors."""
        result = PayRunCalculationResult(
            pay_run_id=uuid4(),
            results={},
            total_gross=Decimal("5000"),
            total_net=Decimal("4000"),
            error_count=2,
        )

        assert result.error_count == 2


class TestEmployeeCalculationContext:
    """Test EmployeeCalculationContext dataclass."""

    def test_context_initialization(self):
        """Context initializes with all required fields."""
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        assert ctx.gross == Decimal("0")
        assert ctx.net == Decimal("0")
        assert ctx.errors == []
        assert ctx.taxable_wages is None

    def test_context_errors_are_mutable(self):
        """Can append errors to context."""
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        ctx.errors.append("Error 1")
        ctx.errors.append("Error 2")

        assert len(ctx.errors) == 2


class TestGarnishmentCalculation:
    """Test garnishment calculation logic."""

    def test_garnishment_with_percent_limit(self):
        """Garnishment respects max_percent limit."""
        engine = PayrollEngine.__new__(PayrollEngine)
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        # Mock garnishment
        garnishment = type("Garnishment", (), {
            "max_percent": Decimal("25"),
            "max_amount": None,
            "order_type": "child_support",
            "case_number": "CS-123",
        })()

        disposable = Decimal("4000")
        result = engine._calculate_garnishment(ctx, garnishment, disposable)

        assert result is not None
        # 25% of 4000 = 1000
        assert result.amount == Decimal("-1000.00")

    def test_garnishment_with_amount_limit(self):
        """Garnishment respects max_amount limit."""
        engine = PayrollEngine.__new__(PayrollEngine)
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        garnishment = type("Garnishment", (), {
            "max_percent": None,
            "max_amount": Decimal("500"),
            "order_type": "creditor",
            "case_number": None,
        })()

        disposable = Decimal("4000")
        result = engine._calculate_garnishment(ctx, garnishment, disposable)

        assert result is not None
        assert result.amount == Decimal("-500")

    def test_garnishment_respects_both_limits(self):
        """Garnishment takes minimum of percent and amount limits."""
        engine = PayrollEngine.__new__(PayrollEngine)
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        garnishment = type("Garnishment", (), {
            "max_percent": Decimal("25"),
            "max_amount": Decimal("500"),
            "order_type": "tax_levy",
            "case_number": "TL-456",
        })()

        disposable = Decimal("4000")
        # 25% of 4000 = 1000, but max_amount is 500
        result = engine._calculate_garnishment(ctx, garnishment, disposable)

        assert result is not None
        assert result.amount == Decimal("-500")

    def test_garnishment_zero_disposable(self):
        """No garnishment when disposable income is zero."""
        engine = PayrollEngine.__new__(PayrollEngine)
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        garnishment = type("Garnishment", (), {
            "max_percent": Decimal("25"),
            "max_amount": None,
            "order_type": "child_support",
            "case_number": "CS-123",
        })()

        result = engine._calculate_garnishment(ctx, garnishment, Decimal("0"))
        assert result is None

    def test_garnishment_negative_disposable(self):
        """No garnishment when disposable income is negative."""
        engine = PayrollEngine.__new__(PayrollEngine)
        ctx = EmployeeCalculationContext(
            employee_id=uuid4(),
            pay_run_id=uuid4(),
            as_of_date=date(2026, 1, 15),
            check_date=date(2026, 1, 20),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 15),
            legal_entity_id=uuid4(),
        )

        garnishment = type("Garnishment", (), {
            "max_percent": Decimal("25"),
            "max_amount": None,
            "order_type": "child_support",
            "case_number": "CS-123",
        })()

        result = engine._calculate_garnishment(ctx, garnishment, Decimal("-100"))
        assert result is None
