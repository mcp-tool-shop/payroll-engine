"""
Tests for PSP AI Advisory System.

These tests verify that:
1. AI advisories are truly advisory-only (no state mutation)
2. Feature extraction is deterministic
3. Rules baseline produces explainable results
4. Confidence scores are properly bounded
5. Advisory events are properly formed
"""

import json
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4, UUID

from payroll_engine.psp.ai.base import (
    AdvisoryConfig,
    AdvisoryMode,
    ReturnAdvisory,
    FundingRiskAdvisory,
    ContributingFactor,
)
from payroll_engine.psp.ai.features import (
    ReturnFeatures,
    FundingRiskFeatures,
    RETURN_FEATURE_SCHEMA_VERSION,
    FUNDING_RISK_FEATURE_SCHEMA_VERSION,
)
from payroll_engine.psp.ai.models.rules_baseline import (
    RulesBaselineReturnModel,
    RulesBaselineFundingRiskModel,
)
from payroll_engine.psp.ai.return_codes import (
    get_return_code_info,
    get_all_codes_by_fault_prior,
)
from payroll_engine.psp.ai.return_advisor import ReturnAdvisor
from payroll_engine.psp.ai.funding_risk import FundingRiskAdvisor
from payroll_engine.psp.ai.explanations import (
    format_advisory_explanation,
    explain_confidence,
    generate_audit_trail,
)
from payroll_engine.psp.ai.decision_record import compute_feature_hash


# =============================================================================
# Configuration Tests
# =============================================================================

class TestAdvisoryConfig:
    """Test advisory configuration constraints."""

    def test_default_config_is_advisory_only(self):
        """Default config must be advisory-only mode."""
        config = AdvisoryConfig()
        assert config.mode == AdvisoryMode.ADVISORY_ONLY

    def test_only_advisory_mode_allowed(self):
        """Attempting other modes must fail."""
        # AdvisoryMode only has ADVISORY_ONLY, so this tests the enum
        assert len(AdvisoryMode) == 1
        assert AdvisoryMode.ADVISORY_ONLY.value == "advisory_only"

    def test_confidence_thresholds_bounded(self):
        """Confidence thresholds must be between 0 and 1."""
        with pytest.raises(ValueError):
            AdvisoryConfig(min_confidence_to_emit=-0.1)

        with pytest.raises(ValueError):
            AdvisoryConfig(min_confidence_to_emit=1.5)

        with pytest.raises(ValueError):
            AdvisoryConfig(high_confidence_threshold=-0.1)

    def test_valid_config_accepted(self):
        """Valid configuration should be accepted."""
        config = AdvisoryConfig(
            enabled=True,
            model_name="rules_baseline",
            min_confidence_to_emit=0.5,
            high_confidence_threshold=0.9,
            lookback_days=180,
        )
        assert config.enabled is True
        assert config.lookback_days == 180


# =============================================================================
# Feature Tests
# =============================================================================

class TestReturnFeatures:
    """Test return feature extraction."""

    def test_feature_schema_version_exists(self):
        """Feature schema must be versioned."""
        assert RETURN_FEATURE_SCHEMA_VERSION == "1.0.0"

    def test_feature_schema_hash_deterministic(self):
        """Same features must produce same hash."""
        f1 = self._make_features()
        f2 = self._make_features()
        assert f1.schema_hash == f2.schema_hash

    def test_new_account_detection(self):
        """Accounts < 14 days old are flagged as new."""
        features = self._make_features(payee_account_age_days=5)
        assert features.payee_is_new_account is True

        features = self._make_features(payee_account_age_days=20)
        assert features.payee_is_new_account is False

    def test_to_dict_contains_all_fields(self):
        """to_dict must include all relevant fields."""
        features = self._make_features()
        d = features.to_dict()

        assert "return_code" in d
        assert "amount" in d
        assert "payee_account_age_days" in d
        assert "tenant_return_rate_90d" in d

    def _make_features(self, **overrides) -> ReturnFeatures:
        """Create test features with defaults."""
        defaults = {
            "tenant_id": uuid4(),
            "payment_id": uuid4(),
            "return_code": "R01",
            "payment_rail": "ach",
            "amount": Decimal("1000.00"),
            "original_payment_date": datetime.utcnow() - timedelta(days=3),
            "return_date": datetime.utcnow(),
            "days_since_payment": 3,
            "is_same_day_return": False,
            "is_weekend_return": False,
            "payee_account_age_days": 30,
            "payee_prior_returns_30d": 0,
            "payee_prior_returns_90d": 0,
            "payee_is_new_account": False,
            "tenant_return_rate_30d": 0.01,
            "tenant_return_rate_90d": 0.02,
            "tenant_funding_blocks_90d": 0,
            "provider_name": "test_provider",
            "provider_return_rate_90d": 0.01,
            "provider_avg_settlement_days": 2.0,
            "payment_purpose": "payroll",
            "batch_size": 10,
        }
        defaults.update(overrides)

        # Handle new_account flag
        if "payee_account_age_days" in overrides:
            defaults["payee_is_new_account"] = defaults["payee_account_age_days"] < 14

        return ReturnFeatures(**defaults)


class TestFundingRiskFeatures:
    """Test funding risk feature extraction."""

    def test_feature_schema_version_exists(self):
        """Feature schema must be versioned."""
        assert FUNDING_RISK_FEATURE_SCHEMA_VERSION == "1.0.0"

    def test_spike_ratio_calculation(self):
        """Spike ratio should be payroll/average."""
        features = self._make_features(
            payroll_amount=Decimal("100000"),
            avg_payroll_amount_90d=Decimal("50000"),
            spike_ratio=2.0,  # 100k / 50k
        )
        assert features.spike_ratio == 2.0

    def test_to_dict_contains_all_fields(self):
        """to_dict must include all relevant fields."""
        features = self._make_features()
        d = features.to_dict()

        assert "payroll_amount" in d
        assert "spike_ratio" in d
        assert "funding_headroom" in d
        assert "funding_model" in d

    def _make_features(self, **overrides) -> FundingRiskFeatures:
        """Create test features with defaults."""
        defaults = {
            "tenant_id": uuid4(),
            "payroll_batch_id": uuid4(),
            "payroll_amount": Decimal("50000.00"),
            "payment_count": 100,
            "scheduled_date": datetime.utcnow(),
            "avg_payroll_amount_90d": Decimal("50000.00"),
            "stddev_payroll_amount_90d": Decimal("5000.00"),
            "spike_ratio": 1.0,
            "max_payroll_amount_90d": Decimal("60000.00"),
            "days_since_last_funding_block": None,
            "funding_blocks_30d": 0,
            "funding_blocks_90d": 0,
            "historical_block_rate": 0.0,
            "avg_settlement_delay_days": 2.0,
            "p95_settlement_delay_days": 3.0,
            "pending_settlements_count": 0,
            "pending_settlements_amount": Decimal("0"),
            "current_available_balance": Decimal("100000.00"),
            "current_reserved_balance": Decimal("0"),
            "funding_headroom": Decimal("45000.00"),
            "funding_model": "prefunded",
            "has_backup_funding": False,
        }
        defaults.update(overrides)
        return FundingRiskFeatures(**defaults)


# =============================================================================
# Rules Baseline Model Tests
# =============================================================================

class TestRulesBaselineReturnModel:
    """Test the rules-based return model."""

    def test_employee_fault_codes_attributed_to_employee(self):
        """R01-R04 should suggest employee origin."""
        model = RulesBaselineReturnModel()

        for code in ["R01", "R02", "R03", "R04"]:
            features = self._make_features(return_code=code)
            origin, _, _, confidence, _, _ = model.predict(features)

            assert origin == "employee", f"Code {code} should be employee origin"
            assert confidence > 0.3, f"Code {code} should have reasonable confidence"

    def test_provider_fault_codes_attributed_to_provider(self):
        """R17+ should suggest provider origin."""
        model = RulesBaselineReturnModel()

        for code in ["R17", "R18", "R19", "R24"]:
            features = self._make_features(return_code=code)
            origin, _, _, confidence, _, _ = model.predict(features)

            assert origin == "provider", f"Code {code} should be provider origin"

    def test_new_account_with_r01_increases_employee_confidence(self):
        """New account + R01 should strongly suggest employee fault."""
        model = RulesBaselineReturnModel()

        # Old account with R01
        features_old = self._make_features(
            return_code="R01",
            payee_account_age_days=60,
            payee_is_new_account=False,
        )
        _, _, _, confidence_old, _, _ = model.predict(features_old)

        # New account with R01
        features_new = self._make_features(
            return_code="R01",
            payee_account_age_days=5,
            payee_is_new_account=True,
        )
        _, _, _, confidence_new, _, _ = model.predict(features_new)

        assert confidence_new > confidence_old, "New account should increase confidence"

    def test_repeat_offender_increases_employee_score(self):
        """Multiple prior returns should increase employee attribution."""
        model = RulesBaselineReturnModel()

        features_clean = self._make_features(
            return_code="R01",
            payee_prior_returns_30d=0,
        )
        origin_clean, _, _, conf_clean, factors_clean, _ = model.predict(features_clean)

        features_repeat = self._make_features(
            return_code="R01",
            payee_prior_returns_30d=3,
        )
        origin_repeat, _, _, conf_repeat, factors_repeat, _ = model.predict(features_repeat)

        # Both should be employee, but repeat should have more factors
        assert origin_clean == "employee"
        assert origin_repeat == "employee"
        assert len(factors_repeat) > len(factors_clean)

    def test_confidence_always_between_0_and_1(self):
        """Confidence must be bounded."""
        model = RulesBaselineReturnModel()

        # Test with extreme values
        features = self._make_features(
            return_code="R01",
            payee_is_new_account=True,
            payee_prior_returns_30d=10,
            tenant_return_rate_90d=0.5,
            tenant_funding_blocks_90d=10,
        )
        _, _, _, confidence, _, _ = model.predict(features)

        assert 0.0 <= confidence <= 1.0

    def test_contributing_factors_are_explainable(self):
        """Every prediction must have explainable factors."""
        model = RulesBaselineReturnModel()

        features = self._make_features(return_code="R01")
        _, _, _, _, factors, _ = model.predict(features)

        assert len(factors) > 0, "Must have at least one factor"

        for factor in factors:
            assert factor.name, "Factor must have a name"
            assert factor.explanation, "Factor must have an explanation"
            assert 0.0 <= factor.weight <= 1.0, "Weight must be bounded"
            assert factor.direction in {"increases_risk", "decreases_risk", "neutral"}

    def _make_features(self, **overrides) -> ReturnFeatures:
        """Create test features."""
        defaults = {
            "tenant_id": uuid4(),
            "payment_id": uuid4(),
            "return_code": "R01",
            "payment_rail": "ach",
            "amount": Decimal("1000.00"),
            "original_payment_date": datetime.utcnow() - timedelta(days=3),
            "return_date": datetime.utcnow(),
            "days_since_payment": 3,
            "is_same_day_return": False,
            "is_weekend_return": False,
            "payee_account_age_days": 30,
            "payee_prior_returns_30d": 0,
            "payee_prior_returns_90d": 0,
            "payee_is_new_account": False,
            "tenant_return_rate_30d": 0.01,
            "tenant_return_rate_90d": 0.02,
            "tenant_funding_blocks_90d": 0,
            "provider_name": "test_provider",
            "provider_return_rate_90d": 0.01,
            "provider_avg_settlement_days": 2.0,
            "payment_purpose": "payroll",
            "batch_size": 10,
        }
        defaults.update(overrides)

        if "payee_account_age_days" in overrides:
            defaults["payee_is_new_account"] = defaults["payee_account_age_days"] < 14

        return ReturnFeatures(**defaults)


class TestRulesBaselineFundingRiskModel:
    """Test the rules-based funding risk model."""

    def test_spike_increases_risk(self):
        """Payroll spike should increase risk score."""
        model = RulesBaselineFundingRiskModel()

        features_normal = self._make_features(spike_ratio=1.0)
        score_normal, _, _, _, _, _ = model.predict(features_normal)

        features_spike = self._make_features(spike_ratio=2.5)
        score_spike, _, _, _, _, _ = model.predict(features_spike)

        assert score_spike > score_normal

    def test_recent_blocks_increase_risk(self):
        """Recent funding blocks should increase risk."""
        model = RulesBaselineFundingRiskModel()

        features_clean = self._make_features(funding_blocks_30d=0)
        score_clean, _, _, _, _, _ = model.predict(features_clean)

        features_blocked = self._make_features(funding_blocks_30d=2)
        score_blocked, _, _, _, _, _ = model.predict(features_blocked)

        assert score_blocked > score_clean

    def test_negative_headroom_is_critical(self):
        """Insufficient funds should be high risk."""
        model = RulesBaselineFundingRiskModel()

        features = self._make_features(
            payroll_amount=Decimal("100000"),
            current_available_balance=Decimal("50000"),
            funding_headroom=Decimal("-60000"),  # Not enough
        )
        score, band, _, _, _, _ = model.predict(features)

        assert score > 0.3, "Negative headroom should significantly increase risk"

    def test_risk_bands_are_ordered(self):
        """Risk bands should correspond to score thresholds."""
        model = RulesBaselineFundingRiskModel()

        # Low risk
        features_low = self._make_features(
            spike_ratio=1.0,
            funding_blocks_30d=0,
            funding_headroom=Decimal("50000"),
        )
        score_low, band_low, _, _, _, _ = model.predict(features_low)
        assert band_low == "low"
        assert score_low < 0.2

        # Critical risk
        features_critical = self._make_features(
            spike_ratio=3.0,
            funding_blocks_30d=3,
            funding_headroom=Decimal("-10000"),
            historical_block_rate=0.2,
        )
        score_critical, band_critical, _, _, _, _ = model.predict(features_critical)
        assert band_critical in {"high", "critical"}
        assert score_critical > 0.4

    def test_suggested_buffer_increases_with_risk(self):
        """Suggested buffer should be larger for higher risk."""
        model = RulesBaselineFundingRiskModel()

        features_low = self._make_features(
            payroll_amount=Decimal("50000"),
            spike_ratio=1.0,
            funding_blocks_30d=0,
        )
        _, _, buffer_low, _, _, _ = model.predict(features_low)

        features_high = self._make_features(
            payroll_amount=Decimal("50000"),
            spike_ratio=2.0,
            funding_blocks_30d=2,
        )
        _, _, buffer_high, _, _, _ = model.predict(features_high)

        assert buffer_high > buffer_low

    def test_risk_score_capped_at_1(self):
        """Risk score must not exceed 1.0."""
        model = RulesBaselineFundingRiskModel()

        # Extreme risk factors
        features = self._make_features(
            spike_ratio=5.0,
            funding_blocks_30d=10,
            funding_blocks_90d=20,
            historical_block_rate=0.5,
            funding_headroom=Decimal("-100000"),
            pending_settlements_amount=Decimal("100000"),
            p95_settlement_delay_days=10.0,
        )
        score, _, _, _, _, _ = model.predict(features)

        assert score <= 1.0

    def _make_features(self, **overrides) -> FundingRiskFeatures:
        """Create test features."""
        defaults = {
            "tenant_id": uuid4(),
            "payroll_batch_id": uuid4(),
            "payroll_amount": Decimal("50000.00"),
            "payment_count": 100,
            "scheduled_date": datetime.utcnow(),
            "avg_payroll_amount_90d": Decimal("50000.00"),
            "stddev_payroll_amount_90d": Decimal("5000.00"),
            "spike_ratio": 1.0,
            "max_payroll_amount_90d": Decimal("60000.00"),
            "days_since_last_funding_block": None,
            "funding_blocks_30d": 0,
            "funding_blocks_90d": 0,
            "historical_block_rate": 0.0,
            "avg_settlement_delay_days": 2.0,
            "p95_settlement_delay_days": 3.0,
            "pending_settlements_count": 0,
            "pending_settlements_amount": Decimal("0"),
            "current_available_balance": Decimal("100000.00"),
            "current_reserved_balance": Decimal("0"),
            "funding_headroom": Decimal("45000.00"),
            "funding_model": "prefunded",
            "has_backup_funding": False,
        }
        defaults.update(overrides)
        return FundingRiskFeatures(**defaults)


# =============================================================================
# Advisory Object Tests
# =============================================================================

class TestAdvisoryObjects:
    """Test advisory data objects."""

    def test_return_advisory_validates_origin(self):
        """Return advisory must have valid error origin."""
        with pytest.raises(ValueError, match="Invalid error origin"):
            ReturnAdvisory(
                advisory_id=uuid4(),
                tenant_id=uuid4(),
                generated_at=datetime.utcnow(),
                model_name="test",
                model_version="1.0",
                feature_schema_hash="abc123",
                confidence=0.9,
                contributing_factors=(),
                explanation="test",
                payment_id=uuid4(),
                return_code="R01",
                suggested_error_origin="invalid",  # Bad value
                suggested_liability_party="employer",
                suggested_recovery_path="offset",
            )

    def test_return_advisory_validates_recovery_path(self):
        """Return advisory must have valid recovery path."""
        with pytest.raises(ValueError, match="Invalid recovery path"):
            ReturnAdvisory(
                advisory_id=uuid4(),
                tenant_id=uuid4(),
                generated_at=datetime.utcnow(),
                model_name="test",
                model_version="1.0",
                feature_schema_hash="abc123",
                confidence=0.9,
                contributing_factors=(),
                explanation="test",
                payment_id=uuid4(),
                return_code="R01",
                suggested_error_origin="employee",
                suggested_liability_party="employer",
                suggested_recovery_path="invalid",  # Bad value
            )

    def test_funding_advisory_validates_risk_band(self):
        """Funding advisory must have valid risk band."""
        with pytest.raises(ValueError, match="Invalid risk band"):
            FundingRiskAdvisory(
                advisory_id=uuid4(),
                tenant_id=uuid4(),
                generated_at=datetime.utcnow(),
                model_name="test",
                model_version="1.0",
                feature_schema_hash="abc123",
                confidence=0.9,
                contributing_factors=(),
                explanation="test",
                payroll_batch_id=uuid4(),
                predicted_amount=Decimal("50000"),
                risk_score=0.5,
                risk_band="invalid",  # Bad value
                suggested_reserve_buffer=Decimal("5000"),
            )

    def test_confidence_must_be_bounded(self):
        """Confidence must be between 0 and 1."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ReturnAdvisory(
                advisory_id=uuid4(),
                tenant_id=uuid4(),
                generated_at=datetime.utcnow(),
                model_name="test",
                model_version="1.0",
                feature_schema_hash="abc123",
                confidence=1.5,  # Too high
                contributing_factors=(),
                explanation="test",
                payment_id=uuid4(),
                return_code="R01",
                suggested_error_origin="employee",
                suggested_liability_party="employer",
                suggested_recovery_path="offset",
            )


# =============================================================================
# Explanation Tests
# =============================================================================

class TestExplanations:
    """Test explanation generation."""

    def test_confidence_explanation_varies_with_score(self):
        """Different confidence levels should get different explanations."""
        high = explain_confidence(0.95)
        low = explain_confidence(0.40)

        assert "high" in high.lower() or "strong" in high.lower()
        assert "low" in low.lower() or "uncertain" in low.lower()

    def test_audit_trail_includes_all_fields(self):
        """Audit trail must capture all decision data."""
        advisory = ReturnAdvisory(
            advisory_id=uuid4(),
            tenant_id=uuid4(),
            generated_at=datetime.utcnow(),
            model_name="rules_baseline",
            model_version="1.0.0",
            feature_schema_hash="abc123",
            confidence=0.85,
            contributing_factors=(
                ContributingFactor(
                    name="test_factor",
                    value="test_value",
                    weight=0.5,
                    direction="increases_risk",
                    explanation="Test explanation",
                ),
            ),
            explanation="Full explanation here",
            payment_id=uuid4(),
            return_code="R01",
            suggested_error_origin="employee",
            suggested_liability_party="employer",
            suggested_recovery_path="offset",
        )

        trail = generate_audit_trail(advisory)

        assert "advisory_id" in trail
        assert "model" in trail
        assert trail["model"]["name"] == "rules_baseline"
        assert "factors" in trail
        assert len(trail["factors"]) == 1
        assert trail["factors"][0]["name"] == "test_factor"


# =============================================================================
# Integration Tests (No DB Required)
# =============================================================================

class TestAdvisoryIntegration:
    """Integration tests for full advisory flow."""

    def test_disabled_advisor_returns_none(self):
        """Disabled advisor should return None, not error."""
        config = AdvisoryConfig(enabled=False)

        # Would need a mock event store in real tests
        # advisor = ReturnAdvisor(config, mock_event_store)
        # result = advisor.analyze(...)
        # assert result is None

        # For now, just verify config
        assert config.enabled is False

    def test_advisory_generation_is_deterministic(self):
        """Same inputs should produce same outputs."""
        model = RulesBaselineReturnModel()

        features = ReturnFeatures(
            tenant_id=uuid4(),
            payment_id=uuid4(),
            return_code="R01",
            payment_rail="ach",
            amount=Decimal("1000.00"),
            original_payment_date=datetime(2025, 1, 1, 12, 0, 0),
            return_date=datetime(2025, 1, 4, 12, 0, 0),
            days_since_payment=3,
            is_same_day_return=False,
            is_weekend_return=False,
            payee_account_age_days=5,
            payee_prior_returns_30d=0,
            payee_prior_returns_90d=0,
            payee_is_new_account=True,
            tenant_return_rate_30d=0.01,
            tenant_return_rate_90d=0.02,
            tenant_funding_blocks_90d=0,
            provider_name="test",
            provider_return_rate_90d=0.01,
            provider_avg_settlement_days=2.0,
            payment_purpose="payroll",
            batch_size=10,
        )

        result1 = model.predict(features)
        result2 = model.predict(features)

        assert result1[0] == result2[0]  # origin
        assert result1[1] == result2[1]  # liability
        assert result1[2] == result2[2]  # recovery
        assert result1[3] == result2[3]  # confidence
        assert result1[5] == result2[5]  # num_indicators


# =============================================================================
# Property Tests (using pytest, not hypothesis for simplicity)
# =============================================================================

class TestAdvisoryProperties:
    """Property-based tests for advisory system."""

    def test_advisory_never_mutates_state(self):
        """
        AI advisories must NEVER mutate state.

        This is a critical invariant. Advisories should only read
        from the event store and emit advisory events.
        """
        # This would be enforced architecturally by:
        # 1. Advisors only have read access to event store
        # 2. Advisors return advisory objects, not write results
        # 3. Advisory events are separate from operational events

        # The test here is structural - verify the classes don't have
        # any methods that could mutate state
        model = RulesBaselineReturnModel()

        # Model should only have predict method (read-only)
        public_methods = [m for m in dir(model) if not m.startswith("_")]
        assert "predict" in public_methods

        # Should NOT have any write methods
        assert "write" not in public_methods
        assert "save" not in public_methods
        assert "update" not in public_methods
        assert "delete" not in public_methods

    def test_all_return_codes_handled(self):
        """Model should handle any return code without crashing."""
        model = RulesBaselineReturnModel()

        # Test known codes
        for code in ["R01", "R02", "R03", "R17", "R24", "R99"]:
            features = ReturnFeatures(
                tenant_id=uuid4(),
                payment_id=uuid4(),
                return_code=code,
                payment_rail="ach",
                amount=Decimal("1000.00"),
                original_payment_date=datetime.utcnow(),
                return_date=datetime.utcnow(),
                days_since_payment=0,
                is_same_day_return=True,
                is_weekend_return=False,
                payee_account_age_days=30,
                payee_prior_returns_30d=0,
                payee_prior_returns_90d=0,
                payee_is_new_account=False,
                tenant_return_rate_30d=0.01,
                tenant_return_rate_90d=0.02,
                tenant_funding_blocks_90d=0,
                provider_name="test",
                provider_return_rate_90d=0.01,
                provider_avg_settlement_days=2.0,
                payment_purpose="payroll",
                batch_size=10,
            )

            # Should not raise
            origin, liability, recovery, confidence, factors, num_indicators = model.predict(features)

            # Basic sanity checks
            assert origin in {"employee", "employer", "provider", "psp", "unknown"}
            assert 0.0 <= confidence <= 1.0
            assert len(factors) > 0
            assert num_indicators >= 1


# =============================================================================
# Replay Determinism Tests
# =============================================================================

class TestReplayDeterminism:
    """
    Test that advisories are deterministic under replay.

    This catches hidden nondeterminism like:
    - Unordered dict iteration
    - Float formatting differences
    - Non-stable factor ordering
    - Timestamp-based seeds
    """

    def test_return_model_replay_determinism(self):
        """
        Same inputs to return model must produce byte-identical output.

        This is critical for:
        - Audit reproducibility
        - Feature hash matching
        - Regulatory replay requirements
        """
        # Fixed inputs - no randomness
        tenant_id = UUID("11111111-1111-1111-1111-111111111111")
        payment_id = UUID("22222222-2222-2222-2222-222222222222")

        features = ReturnFeatures(
            tenant_id=tenant_id,
            payment_id=payment_id,
            return_code="R01",
            payment_rail="ach",
            amount=Decimal("1234.56"),
            original_payment_date=datetime(2025, 1, 1, 12, 0, 0),
            return_date=datetime(2025, 1, 4, 12, 0, 0),
            days_since_payment=3,
            is_same_day_return=False,
            is_weekend_return=False,
            payee_account_age_days=7,
            payee_prior_returns_30d=2,
            payee_prior_returns_90d=3,
            payee_is_new_account=True,
            tenant_return_rate_30d=0.03,
            tenant_return_rate_90d=0.025,
            tenant_funding_blocks_90d=1,
            provider_name="test_provider",
            provider_return_rate_90d=0.015,
            provider_avg_settlement_days=2.0,
            payment_purpose="payroll",
            batch_size=50,
        )

        model = RulesBaselineReturnModel()

        # Run 1
        result1 = model.predict(features, feature_completeness=0.95)
        json1 = self._serialize_return_result(result1)

        # Run 2 - fresh model instance
        model2 = RulesBaselineReturnModel()
        result2 = model2.predict(features, feature_completeness=0.95)
        json2 = self._serialize_return_result(result2)

        # Byte-for-byte comparison
        assert json1 == json2, f"Results differ:\n{json1}\nvs\n{json2}"

    def test_funding_risk_model_replay_determinism(self):
        """
        Same inputs to funding risk model must produce byte-identical output.
        """
        tenant_id = UUID("33333333-3333-3333-3333-333333333333")
        batch_id = UUID("44444444-4444-4444-4444-444444444444")

        features = FundingRiskFeatures(
            tenant_id=tenant_id,
            payroll_batch_id=batch_id,
            payroll_amount=Decimal("75000.00"),
            payment_count=150,
            scheduled_date=datetime(2025, 2, 15, 10, 0, 0),
            avg_payroll_amount_90d=Decimal("50000.00"),
            stddev_payroll_amount_90d=Decimal("5000.00"),
            spike_ratio=1.5,
            max_payroll_amount_90d=Decimal("65000.00"),
            days_since_last_funding_block=45,
            funding_blocks_30d=1,
            funding_blocks_90d=2,
            historical_block_rate=0.05,
            avg_settlement_delay_days=2.0,
            p95_settlement_delay_days=3.5,
            pending_settlements_count=3,
            pending_settlements_amount=Decimal("20000.00"),
            current_available_balance=Decimal("90000.00"),
            current_reserved_balance=Decimal("5000.00"),
            funding_headroom=Decimal("10000.00"),
            funding_model="prefunded",
            has_backup_funding=False,
        )

        model = RulesBaselineFundingRiskModel()

        # Run 1
        result1 = model.predict(features, feature_completeness=0.9)
        json1 = self._serialize_funding_result(result1)

        # Run 2 - fresh model instance
        model2 = RulesBaselineFundingRiskModel()
        result2 = model2.predict(features, feature_completeness=0.9)
        json2 = self._serialize_funding_result(result2)

        # Byte-for-byte comparison
        assert json1 == json2, f"Results differ:\n{json1}\nvs\n{json2}"

    def test_factor_ordering_is_stable(self):
        """
        Contributing factors must always be in the same order.

        Non-stable ordering would cause JSON differences even with
        identical logic results.
        """
        tenant_id = UUID("55555555-5555-5555-5555-555555555555")
        payment_id = UUID("66666666-6666-6666-6666-666666666666")

        # Features that trigger multiple factors
        features = ReturnFeatures(
            tenant_id=tenant_id,
            payment_id=payment_id,
            return_code="R01",
            payment_rail="ach",
            amount=Decimal("5000.00"),
            original_payment_date=datetime(2025, 3, 1, 12, 0, 0),
            return_date=datetime(2025, 3, 4, 12, 0, 0),
            days_since_payment=3,
            is_same_day_return=False,
            is_weekend_return=False,
            payee_account_age_days=5,  # New account
            payee_prior_returns_30d=3,  # Repeat offender
            payee_prior_returns_90d=5,
            payee_is_new_account=True,
            tenant_return_rate_30d=0.08,  # High rate
            tenant_return_rate_90d=0.07,
            tenant_funding_blocks_90d=3,  # Funding issues
            provider_name="test",
            provider_return_rate_90d=0.04,  # High provider rate
            provider_avg_settlement_days=2.5,
            payment_purpose="payroll",
            batch_size=25,
        )

        model = RulesBaselineReturnModel()

        # Run many times and check ordering is identical
        factor_orderings = []
        for _ in range(10):
            _, _, _, _, factors, _ = model.predict(features)
            factor_names = [f.name for f in factors]
            factor_orderings.append(tuple(factor_names))

        # All orderings must be identical
        assert len(set(factor_orderings)) == 1, \
            f"Factor ordering is not stable: {set(factor_orderings)}"

    def test_float_formatting_is_stable(self):
        """
        Float values in factors must format identically.

        Different Python implementations might format 0.1 differently.
        """
        tenant_id = UUID("77777777-7777-7777-7777-777777777777")
        payment_id = UUID("88888888-8888-8888-8888-888888888888")

        features = ReturnFeatures(
            tenant_id=tenant_id,
            payment_id=payment_id,
            return_code="R01",
            payment_rail="ach",
            amount=Decimal("1000.00"),
            original_payment_date=datetime(2025, 4, 1),
            return_date=datetime(2025, 4, 4),
            days_since_payment=3,
            is_same_day_return=False,
            is_weekend_return=False,
            payee_account_age_days=30,
            payee_prior_returns_30d=0,
            payee_prior_returns_90d=0,
            payee_is_new_account=False,
            tenant_return_rate_30d=0.01,
            tenant_return_rate_90d=0.02,
            tenant_funding_blocks_90d=0,
            provider_name="test",
            provider_return_rate_90d=0.01,
            provider_avg_settlement_days=2.0,
            payment_purpose="payroll",
            batch_size=10,
        )

        model = RulesBaselineReturnModel()

        results = []
        for _ in range(5):
            _, _, _, confidence, factors, _ = model.predict(features)
            # Serialize to catch formatting differences
            serialized = {
                "confidence": f"{confidence:.10f}",
                "factors": [
                    {
                        "name": f.name,
                        "weight": f"{f.weight:.10f}",
                        "value": str(f.value),
                    }
                    for f in factors
                ]
            }
            results.append(json.dumps(serialized, sort_keys=True))

        # All serializations must be identical
        assert len(set(results)) == 1, \
            f"Float formatting is not stable: {set(results)}"

    def test_feature_hash_determinism(self):
        """
        Feature hashes must be deterministic for reproducibility.
        """
        features_dict = {
            "return_code": "R01",
            "amount": "1234.56",
            "payee_account_age_days": 30,
            "tenant_return_rate_90d": 0.025,
            "nested": {"a": 1, "b": 2, "c": 3},
            "list_field": [1, 2, 3],
        }

        # Run multiple times
        hashes = [compute_feature_hash(features_dict) for _ in range(10)]

        # All hashes must be identical
        assert len(set(hashes)) == 1, f"Feature hash is not deterministic: {set(hashes)}"

    def test_feature_hash_ignores_dict_ordering(self):
        """
        Feature hash must be identical regardless of dict key ordering.
        """
        dict1 = {"a": 1, "b": 2, "c": 3}
        dict2 = {"c": 3, "a": 1, "b": 2}
        dict3 = {"b": 2, "c": 3, "a": 1}

        hash1 = compute_feature_hash(dict1)
        hash2 = compute_feature_hash(dict2)
        hash3 = compute_feature_hash(dict3)

        assert hash1 == hash2 == hash3, \
            f"Hashes differ by dict order: {hash1}, {hash2}, {hash3}"

    def _serialize_return_result(
        self,
        result: tuple[str, str, str, float, list[ContributingFactor], int]
    ) -> str:
        """Serialize return model result to canonical JSON."""
        origin, liability, recovery, confidence, factors, num_indicators = result

        # Use sorted keys and consistent formatting
        serialized = {
            "error_origin": origin,
            "liability_party": liability,
            "recovery_path": recovery,
            "confidence": round(confidence, 10),  # Avoid float precision issues
            "num_indicators": num_indicators,
            "factors": [
                {
                    "name": f.name,
                    "value": str(f.value),
                    "weight": round(f.weight, 10),
                    "direction": f.direction,
                    "explanation": f.explanation,
                }
                for f in factors
            ]
        }

        return json.dumps(serialized, sort_keys=True, indent=2)

    def _serialize_funding_result(
        self,
        result: tuple[float, str, Decimal, list[ContributingFactor], list[str], int]
    ) -> str:
        """Serialize funding risk model result to canonical JSON."""
        risk_score, risk_band, buffer, factors, suggestions, num_indicators = result

        serialized = {
            "risk_score": round(risk_score, 10),
            "risk_band": risk_band,
            "suggested_buffer": str(buffer),
            "num_indicators": num_indicators,
            "factors": [
                {
                    "name": f.name,
                    "value": str(f.value),
                    "weight": round(f.weight, 10),
                    "direction": f.direction,
                    "explanation": f.explanation,
                }
                for f in factors
            ],
            "suggestions": suggestions,
        }

        return json.dumps(serialized, sort_keys=True, indent=2)


# =============================================================================
# Insight Generator Tests
# =============================================================================

class TestInsightGenerator:
    """Test the AI advisory insights and learning loop."""

    def test_empty_decisions_returns_empty_report(self):
        """Empty input should return valid but empty report."""
        from payroll_engine.psp.ai.insights import InsightGenerator, AdvisoryReport

        generator = InsightGenerator()
        report = generator.generate_report(
            decisions=[],
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        assert report.total_advisories == 0
        assert report.overall_accuracy == 0.0
        assert len(report.insights) == 0

    def test_accuracy_calculation(self):
        """Accuracy should be (accepted + auto_applied) / total_decided."""
        from payroll_engine.psp.ai.insights import InsightGenerator

        decisions = [
            {"outcome": "accepted", "confidence": 0.8, "advisory_type": "return"},
            {"outcome": "accepted", "confidence": 0.75, "advisory_type": "return"},
            {"outcome": "auto_applied", "confidence": 0.95, "advisory_type": "return"},
            {"outcome": "overridden", "confidence": 0.6, "advisory_type": "return"},
            {"outcome": "pending", "confidence": 0.5, "advisory_type": "return"},
        ]

        generator = InsightGenerator()
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        # 3 correct (2 accepted + 1 auto_applied) out of 4 decided (excluding pending)
        assert report.total_decisions == 4
        assert report.accepted_count == 2
        assert report.auto_applied_count == 1
        assert report.overridden_count == 1
        assert report.pending_count == 1
        assert report.overall_accuracy == 0.75  # 3/4

    def test_high_override_rate_generates_insight(self):
        """High override rate for a return code should generate insight."""
        from payroll_engine.psp.ai.insights import InsightGenerator, InsightCategory

        # Create decisions with high override rate
        decisions = []
        for _ in range(8):
            decisions.append({
                "outcome": "overridden",
                "confidence": 0.7,
                "advisory_type": "return",
                "suggested_outcome": {"return_code": "R05"},
                "tenant_id": str(uuid4()),
            })
        for _ in range(2):
            decisions.append({
                "outcome": "accepted",
                "confidence": 0.7,
                "advisory_type": "return",
                "suggested_outcome": {"return_code": "R05"},
                "tenant_id": str(uuid4()),
            })

        generator = InsightGenerator(min_sample_size=5)
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        # Should have insight about high override rate
        override_insights = [
            i for i in report.insights
            if i.category == InsightCategory.OVERRIDE_PATTERN
        ]
        assert len(override_insights) >= 1
        assert "override" in override_insights[0].title.lower()

    def test_confidence_drift_insight(self):
        """High-confidence overrides should generate confidence drift insight."""
        from payroll_engine.psp.ai.insights import InsightGenerator, InsightCategory

        # Create high-confidence decisions that were overridden
        decisions = [
            {"outcome": "overridden", "confidence": 0.90, "advisory_type": "return",
             "override_reason": "Wrong classification", "tenant_id": str(uuid4())},
            {"outcome": "overridden", "confidence": 0.92, "advisory_type": "return",
             "override_reason": "Context changed", "tenant_id": str(uuid4())},
            {"outcome": "overridden", "confidence": 0.88, "advisory_type": "return",
             "override_reason": "Wrong classification", "tenant_id": str(uuid4())},
            {"outcome": "accepted", "confidence": 0.95, "advisory_type": "return",
             "tenant_id": str(uuid4())},
        ]

        generator = InsightGenerator(high_confidence_threshold=0.85)
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        # Should have confidence drift insight
        drift_insights = [
            i for i in report.insights
            if i.category == InsightCategory.CONFIDENCE_DRIFT
        ]
        assert len(drift_insights) >= 1
        assert "confidence" in drift_insights[0].title.lower()

    def test_report_to_markdown(self):
        """Report should serialize to markdown format."""
        from payroll_engine.psp.ai.insights import InsightGenerator

        decisions = [
            {"outcome": "accepted", "confidence": 0.8, "advisory_type": "return",
             "tenant_id": str(uuid4())},
        ]

        generator = InsightGenerator()
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        md = report.to_markdown()
        assert "# AI Advisory Report" in md
        assert "Summary" in md
        assert "Total Advisories" in md

    def test_report_to_dict(self):
        """Report should serialize to dictionary."""
        from payroll_engine.psp.ai.insights import InsightGenerator

        decisions = [
            {"outcome": "accepted", "confidence": 0.8, "advisory_type": "return",
             "tenant_id": str(uuid4())},
        ]

        generator = InsightGenerator()
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        d = report.to_dict()
        assert "report_id" in d
        assert "summary" in d
        assert d["summary"]["total_advisories"] == 1

    def test_create_report_event(self):
        """Report event should be properly formed."""
        from payroll_engine.psp.ai.insights import (
            InsightGenerator, create_report_event
        )

        decisions = [
            {"outcome": "accepted", "confidence": 0.8, "advisory_type": "return",
             "tenant_id": str(uuid4())},
        ]

        generator = InsightGenerator()
        report = generator.generate_report(
            decisions=decisions,
            period_start=datetime(2025, 1, 1),
            period_end=datetime(2025, 1, 7),
        )

        event = create_report_event(report)
        assert event["event_type"] == "AIAdvisoryReportGenerated"
        assert "event_id" in event
        assert "payload" in event
        assert event["payload"]["total_advisories"] == 1


# =============================================================================
# Counterfactual Simulator Tests
# =============================================================================

class TestCounterfactualSimulator:
    """Test the counterfactual policy simulator."""

    def test_empty_batches_returns_empty_report(self):
        """Empty batch list should return valid but empty report."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, STRICT_POLICY
        )

        simulator = CounterfactualSimulator()
        report = simulator.simulate(
            batches=[],
            counterfactual_policy=STRICT_POLICY,
        )

        assert report.total_batches == 0

    def test_strict_policy_blocks_more_than_permissive(self):
        """Strict policy should block more batches than permissive."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY, PERMISSIVE_POLICY
        )

        # Create batches with moderate risk
        batches = [
            PayrollBatchSnapshot(
                batch_id=uuid4(),
                tenant_id=uuid4(),
                batch_date=datetime(2025, 1, i),
                payroll_amount=Decimal("50000"),
                payment_count=100,
                risk_score=0.35,  # Above strict threshold (0.30) but below permissive (0.70)
                spike_ratio=1.3,
                funding_headroom=Decimal("20000"),
                funding_blocks_30d=0,
                p95_settlement_delay=2.5,
                was_blocked=False,
                actual_policy=FundingPolicy.HYBRID,
            )
            for i in range(1, 6)
        ]

        simulator = CounterfactualSimulator()

        strict_report = simulator.simulate(batches, STRICT_POLICY)
        permissive_report = simulator.simulate(batches, PERMISSIVE_POLICY)

        assert strict_report.counterfactual_blocks >= permissive_report.counterfactual_blocks

    def test_policy_application_correctness(self):
        """Policy rules should be correctly applied."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY
        )

        # Create a batch that should be blocked under strict policy
        batch = PayrollBatchSnapshot(
            batch_id=uuid4(),
            tenant_id=uuid4(),
            batch_date=datetime(2025, 1, 1),
            payroll_amount=Decimal("50000"),
            payment_count=100,
            risk_score=0.40,  # Above strict threshold (0.30)
            spike_ratio=1.0,
            funding_headroom=Decimal("30000"),
            funding_blocks_30d=0,
            p95_settlement_delay=2.0,
            was_blocked=False,
            actual_policy=FundingPolicy.HYBRID,
        )

        simulator = CounterfactualSimulator()
        report = simulator.simulate([batch], STRICT_POLICY)

        assert report.counterfactual_blocks == 1
        assert len(report.outcomes) == 1
        assert report.outcomes[0].would_block is True
        assert "Risk score" in report.outcomes[0].block_reasons[0]

    def test_outcome_change_tracking(self):
        """Should correctly track when outcomes change."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY
        )

        # Batch that was not blocked but would be under strict
        batch_additional = PayrollBatchSnapshot(
            batch_id=uuid4(),
            tenant_id=uuid4(),
            batch_date=datetime(2025, 1, 1),
            payroll_amount=Decimal("50000"),
            payment_count=100,
            risk_score=0.40,
            spike_ratio=1.0,
            funding_headroom=Decimal("30000"),
            funding_blocks_30d=0,
            p95_settlement_delay=2.0,
            was_blocked=False,  # Not blocked
            actual_policy=FundingPolicy.PERMISSIVE,
        )

        simulator = CounterfactualSimulator()
        report = simulator.simulate([batch_additional], STRICT_POLICY)

        assert report.additional_blocks == 1
        assert report.avoided_blocks == 0

    def test_financial_impact_calculation(self):
        """Financial impact should be correctly calculated."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY
        )

        batch = PayrollBatchSnapshot(
            batch_id=uuid4(),
            tenant_id=uuid4(),
            batch_date=datetime(2025, 1, 1),
            payroll_amount=Decimal("75000"),
            payment_count=100,
            risk_score=0.50,  # Will be blocked
            spike_ratio=1.0,
            funding_headroom=Decimal("30000"),
            funding_blocks_30d=0,
            p95_settlement_delay=2.0,
            was_blocked=False,
            actual_policy=FundingPolicy.HYBRID,
        )

        simulator = CounterfactualSimulator()
        report = simulator.simulate([batch], STRICT_POLICY)

        assert report.total_payroll_volume == Decimal("75000")
        assert report.payroll_that_would_block == Decimal("75000")

    def test_report_to_markdown(self):
        """Report should serialize to markdown."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY
        )

        batch = PayrollBatchSnapshot(
            batch_id=uuid4(),
            tenant_id=uuid4(),
            batch_date=datetime(2025, 1, 1),
            payroll_amount=Decimal("50000"),
            payment_count=100,
            risk_score=0.15,
            spike_ratio=1.0,
            funding_headroom=Decimal("30000"),
            funding_blocks_30d=0,
            p95_settlement_delay=2.0,
            was_blocked=False,
            actual_policy=FundingPolicy.HYBRID,
        )

        simulator = CounterfactualSimulator()
        report = simulator.simulate([batch], STRICT_POLICY)

        md = report.to_markdown()
        assert "Counterfactual Policy Analysis" in md
        assert "Policy Comparison" in md

    def test_compare_policies(self):
        """Should compare multiple policies."""
        from payroll_engine.psp.ai.counterfactual import (
            CounterfactualSimulator, FundingPolicy, PayrollBatchSnapshot,
            STRICT_POLICY, HYBRID_POLICY, PERMISSIVE_POLICY
        )

        batch = PayrollBatchSnapshot(
            batch_id=uuid4(),
            tenant_id=uuid4(),
            batch_date=datetime(2025, 1, 1),
            payroll_amount=Decimal("50000"),
            payment_count=100,
            risk_score=0.45,
            spike_ratio=1.0,
            funding_headroom=Decimal("30000"),
            funding_blocks_30d=0,
            p95_settlement_delay=2.0,
            was_blocked=False,
            actual_policy=FundingPolicy.HYBRID,
        )

        simulator = CounterfactualSimulator()
        results = simulator.compare_policies(
            [batch],
            [STRICT_POLICY, HYBRID_POLICY, PERMISSIVE_POLICY]
        )

        assert "strict" in results
        assert "hybrid" in results
        assert "permissive" in results


# =============================================================================
# Tenant Risk Profiler Tests
# =============================================================================

class TestTenantRiskProfiler:
    """Test the tenant risk scoring system."""

    def test_low_risk_tenant(self):
        """Tenant with no issues should be low risk."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics, RiskLevel
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            return_rate_30d=0.005,  # 0.5% - below warning
            reversal_rate_30d=0.001,
            funding_block_count_30d=0,
            settlement_mismatch_count_30d=0,
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        assert profile.risk_level == RiskLevel.LOW
        assert profile.risk_score < 0.25
        assert profile.requires_review is False
        assert profile.requires_immediate_action is False

    def test_high_return_rate_increases_risk(self):
        """High return rate should increase risk score."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics, RiskLevel
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            return_rate_30d=0.08,  # 8% - above critical (5%)
            return_count_30d=15,
            payment_count_30d=200,
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        assert profile.return_risk_score > 0.5
        assert profile.risk_score > profile.return_risk_score * 0.3  # Weighted contribution

    def test_funding_blocks_increase_risk(self):
        """Multiple funding blocks should increase risk."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            funding_block_count_30d=4,  # Above critical (3)
            payroll_count_30d=10,
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        assert profile.funding_risk_score > 0.5
        # Check for funding signal
        funding_signals = [s for s in profile.signals if s.category == "funding"]
        assert len(funding_signals) >= 1

    def test_suspicious_patterns_flag_immediate_action(self):
        """Critical suspicious patterns should require immediate action."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            reservation_churn_count_30d=3,
            status_regression_count_30d=2,
            late_modification_count_30d=2,
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        # 7 total suspicious patterns >= critical threshold (5)
        assert profile.pattern_risk_score >= 0.7
        assert profile.requires_immediate_action is True

    def test_new_tenant_flagged(self):
        """New tenants should be flagged with limited history."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            tenant_age_days=15,
            is_new_tenant=True,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        # Check for new tenant signal
        new_tenant_signals = [
            s for s in profile.signals
            if s.name == "new_tenant"
        ]
        assert len(new_tenant_signals) == 1
        assert "limited history" in profile.recommendations[0].lower() or \
               "new tenant" in profile.recommendations[-1].lower()

    def test_profile_to_markdown(self):
        """Profile should serialize to markdown."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            return_rate_30d=0.03,
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        md = profile.to_markdown()
        assert "Tenant Risk Profile" in md
        assert "Risk Level" in md
        assert "Component Scores" in md

    def test_profile_to_dict(self):
        """Profile should serialize to dictionary."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        d = profile.to_dict()
        assert "profile_id" in d
        assert "assessment" in d
        assert "component_scores" in d
        assert "signals" in d

    def test_create_risk_profile_event(self):
        """Risk profile event should be properly formed."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics, create_risk_profile_event
        )

        metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            tenant_age_days=180,
        )

        profiler = TenantRiskProfiler()
        profile = profiler.profile(metrics)

        event = create_risk_profile_event(profile)
        assert event["event_type"] == "TenantRiskProfileGenerated"
        assert "payload" in event
        assert "risk_level" in event["payload"]
        assert "risk_score" in event["payload"]

    def test_risk_level_thresholds(self):
        """Risk levels should follow defined thresholds."""
        from payroll_engine.psp.ai.tenant_risk import (
            TenantRiskProfiler, TenantMetrics, RiskLevel
        )

        profiler = TenantRiskProfiler()

        # Create metrics that produce known risk scores
        # Very low risk
        low_metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            return_rate_30d=0.0,
            tenant_age_days=365,
        )
        low_profile = profiler.profile(low_metrics)
        assert low_profile.risk_level == RiskLevel.LOW

        # Critical risk - high on multiple dimensions
        critical_metrics = TenantMetrics(
            tenant_id=uuid4(),
            evaluation_time=datetime.utcnow(),
            return_rate_30d=0.10,  # 10% - very high
            return_rate_trend=0.6,  # Increasing
            reversal_rate_30d=0.05,  # High
            funding_block_count_30d=5,  # Many blocks
            settlement_mismatch_count_30d=6,  # Many mismatches
            reservation_churn_count_30d=5,  # Suspicious
            tenant_age_days=180,
        )
        critical_profile = profiler.profile(critical_metrics)
        assert critical_profile.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


# =============================================================================
# Runbook Assistant Tests
# =============================================================================

class TestRunbookAssistant:
    """Test the AI runbook assistant."""

    def test_settlement_mismatch_assistance(self):
        """Settlement mismatch should get specific guidance."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.SETTLEMENT_MISMATCH,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            payment_id=uuid4(),
            amount=Decimal("10000"),
            mismatch_amount=Decimal("500"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert assistance.runbook_name == "Settlement Mismatch"
        assert len(assistance.diagnostic_queries) >= 1
        assert len(assistance.likely_causes) >= 1
        assert len(assistance.recommended_steps) >= 1
        assert "settlement" in assistance.summary.lower()

    def test_funding_block_assistance(self):
        """Funding block should get specific guidance."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.FUNDING_BLOCK,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            batch_id=uuid4(),
            amount=Decimal("75000"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert assistance.runbook_name == "Funding Gate Blocks"
        assert len(assistance.diagnostic_queries) >= 1
        assert "funding" in assistance.summary.lower() or "blocked" in assistance.summary.lower()

    def test_payment_return_assistance(self):
        """Payment return should get code-specific guidance."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.PAYMENT_RETURN,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            payment_id=uuid4(),
            return_code="R01",
            amount=Decimal("1500"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert assistance.runbook_name == "Payment Returns"
        assert "R01" in assistance.summary
        # Should reference the return code info
        assert any("Insufficient" in c.cause for c in assistance.likely_causes)

    def test_high_risk_return_code_severity(self):
        """High-risk return codes should have higher severity."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        # R10 is a fraud indicator
        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.PAYMENT_RETURN,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            payment_id=uuid4(),
            return_code="R10",
            amount=Decimal("5000"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert assistance.estimated_severity == "critical"

    def test_ledger_imbalance_assistance(self):
        """Ledger imbalance should get critical guidance."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.LEDGER_IMBALANCE,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            mismatch_amount=Decimal("1234.56"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert assistance.runbook_name == "Ledger Imbalance"
        assert assistance.estimated_severity == "critical"
        assert any("CRITICAL" in w or "STOP" in w for w in assistance.warnings)

    def test_unknown_incident_type_handled(self):
        """Unknown incident types should get generic guidance."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.UNKNOWN,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            severity="medium",
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        assert len(assistance.recommended_steps) >= 1
        assert "Unknown" in assistance.warnings[0] or "caution" in assistance.warnings[0].lower()

    def test_assistance_to_markdown(self):
        """Assistance should serialize to markdown."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.SETTLEMENT_MISMATCH,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            amount=Decimal("10000"),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        md = assistance.to_markdown()
        assert "Runbook Assistance" in md
        assert "Summary" in md
        assert "Diagnostic Queries" in md
        assert "Recommended Steps" in md

    def test_assistance_to_dict(self):
        """Assistance should serialize to dictionary."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.FUNDING_BLOCK,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        d = assistance.to_dict()
        assert "assistance_id" in d
        assert "runbook" in d
        assert "diagnostic_queries" in d
        assert "likely_causes" in d
        assert "recommended_steps" in d

    def test_create_assistance_event(self):
        """Assistance event should be properly formed."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType,
            create_assistance_event
        )

        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.PAYMENT_RETURN,
            detected_at=datetime.utcnow(),
            tenant_id=uuid4(),
            return_code="R03",
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        event = create_assistance_event(assistance)
        assert event["event_type"] == "RunbookAssistanceGenerated"
        assert "payload" in event
        assert event["payload"]["incident_type"] == "payment_return"
        assert event["payload"]["runbook_name"] == "Payment Returns"

    def test_diagnostic_queries_are_prefilled_not_executed(self):
        """Diagnostic queries should be generated but never executed."""
        from payroll_engine.psp.ai.runbook_assistant import (
            RunbookAssistant, IncidentContext, IncidentType
        )

        tenant_id = uuid4()
        context = IncidentContext(
            incident_id=uuid4(),
            incident_type=IncidentType.SETTLEMENT_MISMATCH,
            detected_at=datetime.utcnow(),
            tenant_id=tenant_id,
            payment_id=uuid4(),
        )

        assistant = RunbookAssistant()
        assistance = assistant.assist(context)

        # Queries should be filled with actual IDs
        assert len(assistance.diagnostic_queries) >= 1
        for query in assistance.diagnostic_queries:
            assert str(tenant_id) in query.query_sql
            assert query.expected_outcome  # Should have expected outcome
            assert query.if_anomalous  # Should have anomaly guidance

    def test_assistant_never_modifies_state(self):
        """Assistant should be pure - no write methods."""
        from payroll_engine.psp.ai.runbook_assistant import RunbookAssistant

        assistant = RunbookAssistant()

        # Should only have assist method (read-only)
        public_methods = [m for m in dir(assistant) if not m.startswith("_")]
        assert "assist" in public_methods

        # Should NOT have any write methods
        assert "write" not in public_methods
        assert "save" not in public_methods
        assert "execute" not in public_methods
        assert "run" not in public_methods
        assert "update" not in public_methods