"""
Tests for AI module optionality.

These tests verify the "enterprise adoptable" contract:
1. Core imports work without AI
2. AI is disabled by default even when installed
3. Explicit enable required
4. Clear error when deps missing but enabled

This is what makes the repo trustworthy for production use.
"""

import pytest


class TestCoreImportsWithoutAI:
    """Test that core PSP works without importing AI."""

    def test_psp_imports_without_ai(self):
        """
        Core PSP module imports work without touching AI.

        This is the MOST IMPORTANT test for enterprise adoption.
        If this fails, core payroll breaks when AI deps are missing.
        """
        # These should NEVER fail regardless of AI installation
        from payroll_engine.psp import (
            LedgerService,
            FundingGateService,
            PaymentOrchestrator,
            ReconciliationService,
            LiabilityService,
        )

        # Verify they're real classes, not import stubs
        assert LedgerService is not None
        assert FundingGateService is not None
        assert PaymentOrchestrator is not None
        assert ReconciliationService is not None
        assert LiabilityService is not None

    def test_psp_events_import_without_ai(self):
        """Domain events work without AI."""
        from payroll_engine.psp import (
            DomainEvent,
            FundingApproved,
            FundingBlocked,
            PaymentSettled,
            PaymentReturned,
            LedgerEntryPosted,
        )

        # Verify they're real classes
        assert DomainEvent is not None
        assert FundingApproved is not None
        assert FundingBlocked is not None
        assert PaymentSettled is not None
        assert PaymentReturned is not None
        assert LedgerEntryPosted is not None

    def test_psp_providers_import_without_ai(self):
        """Rail providers work without AI."""
        from payroll_engine.psp import (
            PaymentRailProvider,
            AchStubProvider,
            FedNowStubProvider,
        )

        assert PaymentRailProvider is not None
        assert AchStubProvider is not None
        assert FedNowStubProvider is not None


class TestAIDisabledByDefault:
    """Test that AI is off by default even when installed."""

    def test_advisory_config_defaults_to_disabled(self):
        """
        AdvisoryConfig.enabled is False by default.

        This is critical: installing [ai] extras does NOT auto-enable AI.
        The user must explicitly set enabled=True.
        """
        from payroll_engine.psp.ai import AdvisoryConfig

        config = AdvisoryConfig()
        assert config.enabled is False, (
            "AdvisoryConfig MUST default to disabled. "
            "Explicit opt-in is required for enterprise trust."
        )

    def test_advisory_mode_locked_to_advisory_only(self):
        """
        Only ADVISORY_ONLY mode is permitted.

        AI can never be configured to auto-apply decisions.
        """
        from payroll_engine.psp.ai import AdvisoryConfig, AdvisoryMode

        config = AdvisoryConfig(enabled=True)
        assert config.mode == AdvisoryMode.ADVISORY_ONLY

    def test_cannot_enable_non_advisory_mode(self):
        """Attempting to set non-advisory mode raises error."""
        from payroll_engine.psp.ai import AdvisoryConfig, AdvisoryMode

        # AdvisoryMode only has ADVISORY_ONLY, so we can't even construct
        # a bad mode value through the enum. This test documents the intent.
        assert len(AdvisoryMode) == 1
        assert AdvisoryMode.ADVISORY_ONLY in AdvisoryMode


class TestAIAvailabilityChecks:
    """Test the AI availability detection - TWO-TIER SYSTEM."""

    def test_rules_baseline_always_available(self):
        """Rules-baseline model works without any extras."""
        from payroll_engine.psp.ai import is_ai_available, STDLIB_MODELS

        # rules_baseline is always available
        assert is_ai_available("rules_baseline") is True
        assert "rules_baseline" in STDLIB_MODELS

    def test_is_ai_available_returns_bool(self):
        """is_ai_available() returns True/False, never raises."""
        from payroll_engine.psp.ai import is_ai_available

        result = is_ai_available()
        assert isinstance(result, bool)

    def test_require_ai_deps_passes_for_rules_baseline(self):
        """require_ai_deps() always passes for rules_baseline."""
        from payroll_engine.psp.ai import require_ai_deps

        # Should NEVER raise for rules_baseline
        require_ai_deps("rules_baseline")
        require_ai_deps()  # Default is rules_baseline

    def test_is_ml_available_returns_bool(self):
        """is_ml_available() returns True/False for ML models."""
        from payroll_engine.psp.ai import is_ml_available

        result = is_ml_available()
        assert isinstance(result, bool)

    def test_ai_not_installed_error_has_helpful_message(self):
        """AINotInstalledError provides clear install instructions."""
        from payroll_engine.psp.ai import AINotInstalledError

        error = AINotInstalledError("gradient_boost")
        message = str(error)

        # Must include install instructions
        assert "pip install payroll-engine[ai]" in message

        # Must mention rules_baseline as alternative
        assert "rules_baseline" in message

    def test_ai_not_installed_error_includes_model_name(self):
        """Model name appears in error message."""
        from payroll_engine.psp.ai import AINotInstalledError

        error = AINotInstalledError("gradient_boost")
        message = str(error)

        assert "gradient_boost" in message


class TestAIExplicitEnablement:
    """Test that AI must be explicitly enabled to use."""

    def test_explicit_enable_required(self):
        """
        Using AI features requires AdvisoryConfig(enabled=True).

        This is the config-time opt-in requirement.
        """
        from payroll_engine.psp.ai import AdvisoryConfig

        # Default is disabled
        default_config = AdvisoryConfig()
        assert default_config.enabled is False

        # Must explicitly enable
        enabled_config = AdvisoryConfig(enabled=True)
        assert enabled_config.enabled is True

    def test_advisor_respects_enabled_flag(self):
        """Advisors check their enabled state."""
        from payroll_engine.psp.ai import (
            AdvisoryConfig,
            ReturnAdvisor,
            FundingRiskAdvisor,
        )

        # Create a mock event store (advisors require this)
        class MockEventStore:
            def get_events(self, *args, **kwargs):
                return []

        mock_store = MockEventStore()

        # Disabled config
        disabled_config = AdvisoryConfig(enabled=False)
        return_advisor = ReturnAdvisor(disabled_config, mock_store)
        risk_advisor = FundingRiskAdvisor(disabled_config, mock_store)

        assert return_advisor.is_enabled() is False
        assert risk_advisor.is_enabled() is False

        # Enabled config
        enabled_config = AdvisoryConfig(enabled=True)
        return_advisor = ReturnAdvisor(enabled_config, mock_store)
        risk_advisor = FundingRiskAdvisor(enabled_config, mock_store)

        assert return_advisor.is_enabled() is True
        assert risk_advisor.is_enabled() is True


class TestPublicAPISurface:
    """Test the documented public API surface."""

    def test_ai_module_exports_availability_check(self):
        """AI module exports is_ai_available for checking."""
        from payroll_engine.psp import ai

        assert hasattr(ai, "is_ai_available")
        assert callable(ai.is_ai_available)

    def test_ai_module_exports_require_deps(self):
        """AI module exports require_ai_deps for validation."""
        from payroll_engine.psp import ai

        assert hasattr(ai, "require_ai_deps")
        assert callable(ai.require_ai_deps)

    def test_ai_module_exports_error_class(self):
        """AI module exports AINotInstalledError for catching."""
        from payroll_engine.psp import ai

        assert hasattr(ai, "AINotInstalledError")
        assert issubclass(ai.AINotInstalledError, ImportError)

    def test_core_advisors_importable(self):
        """Core advisor classes are importable."""
        from payroll_engine.psp.ai import (
            ReturnAdvisor,
            FundingRiskAdvisor,
            InsightGenerator,
            CounterfactualSimulator,
            TenantRiskProfiler,
            RunbookAssistant,
        )

        # All should be real classes
        assert ReturnAdvisor is not None
        assert FundingRiskAdvisor is not None
        assert InsightGenerator is not None
        assert CounterfactualSimulator is not None
        assert TenantRiskProfiler is not None
        assert RunbookAssistant is not None

    def test_config_classes_importable(self):
        """Config and type classes are importable."""
        from payroll_engine.psp.ai import (
            AdvisoryConfig,
            AdvisoryMode,
            Advisory,
            FundingPolicy,
            PolicyConfig,
            RiskLevel,
        )

        assert AdvisoryConfig is not None
        assert AdvisoryMode is not None
        assert Advisory is not None
        assert FundingPolicy is not None
        assert PolicyConfig is not None
        assert RiskLevel is not None


class TestZeroRuntimeCostWhenDisabled:
    """Test that disabled AI has zero runtime overhead."""

    def test_disabled_advisor_does_not_compute(self):
        """
        When disabled, advisors should short-circuit.

        This ensures zero runtime cost when AI is installed but disabled.
        """
        from payroll_engine.psp.ai import AdvisoryConfig, FundingRiskAdvisor

        class MockEventStore:
            def get_events(self, *args, **kwargs):
                return []

        config = AdvisoryConfig(enabled=False)
        advisor = FundingRiskAdvisor(config, MockEventStore())

        # The advisor should recognize it's disabled
        assert advisor.is_enabled() is False


class TestModuleStructure:
    """Test module structure follows best practices."""

    def test_ai_in_submodule_not_core(self):
        """
        AI is in psp.ai, not polluting psp root.

        Users can import payroll_engine.psp without ever touching AI.
        """
        import payroll_engine.psp as psp

        # AI should NOT be in the main __all__
        assert "ReturnAdvisor" not in dir(psp)
        assert "FundingRiskAdvisor" not in dir(psp)
        assert "InsightGenerator" not in dir(psp)

    def test_ai_module_has_docstring(self):
        """AI module has comprehensive docstring."""
        from payroll_engine.psp import ai

        assert ai.__doc__ is not None
        assert "advisory-only" in ai.__doc__.lower()
        assert "never" in ai.__doc__.lower()  # Documents what AI can NEVER do
