"""
PSP AI Advisory Engine

CRITICAL CONSTRAINT: AI is advisory-only.

This module provides machine learning and rules-based advisors that:
- Analyze domain events
- Predict funding risks
- Suggest liability attribution for returns
- Generate human-readable explanations
- Generate operational insights and reports
- Simulate policy counterfactuals
- Score tenant risk
- Assist with runbook execution

AI may NEVER:
- Move money
- Write ledger entries
- Override funding gates
- Decide settlement truth
- Mutate any PSP state

AI may ONLY:
- Read from event store
- Emit advisory events
- Provide recommendations for human/policy review

OPTIONALITY:
- AI is OFF by default (AdvisoryConfig.enabled=False)
- Install with: pip install payroll-engine[ai]
- Explicitly enable: AdvisoryConfig(enabled=True)

If you don't need AI, you can still import payroll_engine.psp
without any AI dependencies or runtime cost.
"""

# ===========================================================================
# Optionality support - always importable
# ===========================================================================
from payroll_engine.psp.ai._optional import (
    is_ai_available,
    is_ml_available,
    require_ai_deps,
    AINotInstalledError,
    STDLIB_MODELS,
)

# ===========================================================================
# Core types - always importable (no external deps)
# ===========================================================================
from payroll_engine.psp.ai.base import (
    AdvisoryConfig,
    AdvisoryMode,
    Advisory,
)
from payroll_engine.psp.ai.features import (
    ReturnFeatures,
    FundingRiskFeatures,
    FeatureExtractor,
)
from payroll_engine.psp.ai.return_advisor import ReturnAdvisor
from payroll_engine.psp.ai.funding_risk import FundingRiskAdvisor

# ===========================================================================
# Learning loop and insights
# ===========================================================================
from payroll_engine.psp.ai.insights import (
    InsightGenerator,
    AdvisoryReport,
    Insight,
    InsightSeverity,
    InsightCategory,
    create_report_event,
)

# ===========================================================================
# Counterfactual simulation
# ===========================================================================
from payroll_engine.psp.ai.counterfactual import (
    CounterfactualSimulator,
    CounterfactualReport,
    PayrollBatchSnapshot,
    FundingPolicy,
    PolicyConfig,
    get_policy_config,
    STRICT_POLICY,
    HYBRID_POLICY,
    PERMISSIVE_POLICY,
)

# ===========================================================================
# Tenant risk scoring
# ===========================================================================
from payroll_engine.psp.ai.tenant_risk import (
    TenantRiskProfiler,
    TenantRiskProfile,
    TenantMetrics,
    RiskLevel,
    RiskSignal,
    create_risk_profile_event,
)

# ===========================================================================
# Runbook assistance
# ===========================================================================
from payroll_engine.psp.ai.runbook_assistant import (
    RunbookAssistant,
    RunbookAssistance,
    IncidentContext,
    IncidentType,
    create_assistance_event,
)

__all__ = [
    # Optionality
    "is_ai_available",
    "is_ml_available",
    "require_ai_deps",
    "AINotInstalledError",
    "STDLIB_MODELS",
    # Core
    "AdvisoryConfig",
    "AdvisoryMode",
    "Advisory",
    "ReturnFeatures",
    "FundingRiskFeatures",
    "FeatureExtractor",
    "ReturnAdvisor",
    "FundingRiskAdvisor",
    # Insights
    "InsightGenerator",
    "AdvisoryReport",
    "Insight",
    "InsightSeverity",
    "InsightCategory",
    "create_report_event",
    # Counterfactual
    "CounterfactualSimulator",
    "CounterfactualReport",
    "PayrollBatchSnapshot",
    "FundingPolicy",
    "PolicyConfig",
    "get_policy_config",
    "STRICT_POLICY",
    "HYBRID_POLICY",
    "PERMISSIVE_POLICY",
    # Tenant risk
    "TenantRiskProfiler",
    "TenantRiskProfile",
    "TenantMetrics",
    "RiskLevel",
    "RiskSignal",
    "create_risk_profile_event",
    # Runbook assistance
    "RunbookAssistant",
    "RunbookAssistance",
    "IncidentContext",
    "IncidentType",
    "create_assistance_event",
]
