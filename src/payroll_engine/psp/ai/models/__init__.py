"""
PSP AI Models

Available models:
- rules_baseline: Deterministic rules-based model (recommended to start)
- sklearn_model: Optional ML model (requires training data)
"""

from payroll_engine.psp.ai.models.rules_baseline import (
    RulesBaselineReturnModel,
    RulesBaselineFundingRiskModel,
)

__all__ = [
    "RulesBaselineReturnModel",
    "RulesBaselineFundingRiskModel",
]
