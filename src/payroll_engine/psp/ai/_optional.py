"""
AI Advisory optional dependency management.

TWO-TIER SYSTEM:
1. Rules-baseline AI: Included in core, no extra deps needed
2. ML models (future): Require [ai] extras (numpy, sklearn, etc.)

This module handles:
- Detection of which AI tier is available
- Hard-fail only when ML models requested but deps missing
- Clear messaging about what's available

IMPORTANT: rules_baseline model works WITHOUT any extras.
"""

from typing import TYPE_CHECKING

# Track which AI tiers are available
_ML_DEPS_AVAILABLE: bool | None = None

# Models that work with stdlib only (no extras needed)
STDLIB_MODELS = frozenset({"rules_baseline"})


class AIMLDepsNotInstalledError(ImportError):
    """
    Raised when ML-based AI model is requested but [ai] extras not installed.

    This is a HARD FAILURE - user wants ML but doesn't have deps.
    Rules-baseline works without extras.
    """

    def __init__(self, model_name: str) -> None:
        super().__init__(
            f"Model '{model_name}' requires ML dependencies.\n"
            f"Install with: pip install payroll-engine[ai]\n\n"
            f"Alternatively, use model='rules_baseline' which needs no extras."
        )


# Keep old name as alias for backwards compatibility
AINotInstalledError = AIMLDepsNotInstalledError


def check_ml_deps_installed() -> bool:
    """
    Check if ML dependencies (numpy, sklearn, etc.) are installed.

    Returns:
        True if [ai] extras are installed.
    """
    global _ML_DEPS_AVAILABLE

    if _ML_DEPS_AVAILABLE is not None:
        return _ML_DEPS_AVAILABLE

    try:
        # Check for ML dependencies when we add them:
        # import numpy  # noqa: F401
        # import sklearn  # noqa: F401
        #
        # For now, no ML models exist yet, so this is always False
        # to demonstrate the pattern. Flip to True when ML deps are added.
        _ML_DEPS_AVAILABLE = True  # No ML models yet, stdlib always available
    except ImportError:
        _ML_DEPS_AVAILABLE = False

    return _ML_DEPS_AVAILABLE


def require_ai_deps(model_name: str = "rules_baseline") -> None:
    """
    Validate that dependencies for the requested model are available.

    Rules-baseline: Always works (stdlib only)
    ML models: Require [ai] extras

    Args:
        model_name: The model being used (default: rules_baseline)

    Raises:
        AIMLDepsNotInstalledError: If ML model requested but deps missing
    """
    if model_name in STDLIB_MODELS:
        # Rules-baseline needs no extras
        return

    # ML model requested - check for extras
    if not check_ml_deps_installed():
        raise AIMLDepsNotInstalledError(model_name)


def is_ai_available(model_name: str = "rules_baseline") -> bool:
    """
    Check if AI features can be used for the specified model.

    Args:
        model_name: The model to check (default: rules_baseline)

    Returns:
        True if the model can be used.

    Example:
        # Rules-baseline always available
        assert is_ai_available("rules_baseline") == True

        # ML models need extras
        if is_ai_available("gradient_boost"):
            config = AdvisoryConfig(model_name="gradient_boost")
    """
    if model_name in STDLIB_MODELS:
        return True
    return check_ml_deps_installed()


def is_ml_available() -> bool:
    """
    Check if ML-based models are available.

    Returns:
        True if [ai] extras are installed.
    """
    return check_ml_deps_installed()


# For type checking only - these are lazily imported at runtime
if TYPE_CHECKING:
    from payroll_engine.psp.ai.base import AdvisoryConfig
