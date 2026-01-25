"""
Return Code Reference Table.

Data-driven return code classification with honest uncertainty.

This is NOT a hard classification - it's a "default hypothesis" with
ambiguity scores. Context (account age, authorization history, etc.)
must be considered alongside these priors.

NACHA return codes don't map cleanly to fault categories. This table
provides:
- fault_prior: Most likely fault party (hypothesis, not truth)
- ambiguity: How uncertain this classification is (low/medium/high)
- confidence_ceiling: Maximum confidence for rules-based attribution
- recommended_actions: What ops should consider
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReturnCodeInfo:
    """Information about a return code."""
    code: str
    description: str
    fault_prior: str  # employee, employer, provider, psp, mixed, unknown
    ambiguity: str  # low, medium, high
    confidence_ceiling: float  # Max confidence for rules-based model
    category: str  # account, authorization, processing, administrative, other
    recommended_actions: tuple[str, ...]
    notes: Optional[str] = None


# =============================================================================
# NACHA Return Code Reference
# =============================================================================
# Sources:
# - NACHA Operating Rules
# - Industry experience
#
# Ambiguity levels:
# - low: Code strongly indicates fault party
# - medium: Code suggests fault but context matters
# - high: Code is ambiguous, investigation required
#
# Confidence ceilings:
# - Rules-based models should not exceed these
# - ML models with calibration may go higher
# =============================================================================

RETURN_CODE_REFERENCE: dict[str, ReturnCodeInfo] = {
    # =========================================================================
    # R01-R04: Account Issues
    # =========================================================================
    "R01": ReturnCodeInfo(
        code="R01",
        description="Insufficient Funds",
        fault_prior="employee",
        ambiguity="medium",  # Could be timing, could be chronic
        confidence_ceiling=0.70,
        category="account",
        recommended_actions=(
            "Verify employee account status",
            "Check if this is a pattern for this payee",
            "Consider prenote for future payments",
        ),
        notes="May indicate temporary timing issue vs. chronic insufficiency",
    ),
    "R02": ReturnCodeInfo(
        code="R02",
        description="Account Closed",
        fault_prior="employee",
        ambiguity="low",  # Clear signal
        confidence_ceiling=0.80,
        category="account",
        recommended_actions=(
            "Request updated bank info from employee",
            "Flag account for verification before next payment",
        ),
    ),
    "R03": ReturnCodeInfo(
        code="R03",
        description="No Account / Unable to Locate Account",
        fault_prior="employee",
        ambiguity="medium",  # Could be data entry error
        confidence_ceiling=0.65,
        category="account",
        recommended_actions=(
            "Verify account number with employee",
            "Check for transposition errors",
            "Prenote recommended",
        ),
        notes="May be employer data entry error if account is new",
    ),
    "R04": ReturnCodeInfo(
        code="R04",
        description="Invalid Account Number Structure",
        fault_prior="mixed",  # Could be employee or data entry
        ambiguity="medium",
        confidence_ceiling=0.60,
        category="account",
        recommended_actions=(
            "Verify account number format",
            "Check for missing digits",
            "Validate against routing number requirements",
        ),
        notes="Often a data entry error - check who entered the data",
    ),

    # =========================================================================
    # R05-R09: Authorization Issues
    # =========================================================================
    "R05": ReturnCodeInfo(
        code="R05",
        description="Unauthorized Debit to Consumer Account",
        fault_prior="mixed",
        ambiguity="high",  # Very context-dependent
        confidence_ceiling=0.50,
        category="authorization",
        recommended_actions=(
            "Review authorization records",
            "Contact employee to verify",
            "Check if this is a disputed payment",
            "Investigate potential fraud",
        ),
        notes="High ambiguity - could be fraud, dispute, or legitimate error",
    ),
    "R06": ReturnCodeInfo(
        code="R06",
        description="Returned per ODFI's Request",
        fault_prior="provider",
        ambiguity="medium",
        confidence_ceiling=0.55,
        category="authorization",
        recommended_actions=(
            "Contact originating bank",
            "Review the specific reason",
        ),
        notes="Bank-initiated return, underlying reason varies",
    ),
    "R07": ReturnCodeInfo(
        code="R07",
        description="Authorization Revoked by Customer",
        fault_prior="employee",
        ambiguity="low",
        confidence_ceiling=0.75,
        category="authorization",
        recommended_actions=(
            "Confirm with employee if intentional",
            "Update payment method",
            "Document revocation",
        ),
    ),
    "R08": ReturnCodeInfo(
        code="R08",
        description="Payment Stopped",
        fault_prior="employee",
        ambiguity="low",
        confidence_ceiling=0.75,
        category="authorization",
        recommended_actions=(
            "Confirm stop payment was intentional",
            "Investigate if potential dispute",
        ),
    ),
    "R09": ReturnCodeInfo(
        code="R09",
        description="Uncollected Funds",
        fault_prior="employee",
        ambiguity="medium",
        confidence_ceiling=0.65,
        category="account",
        recommended_actions=(
            "Wait for funds to clear",
            "Consider retry timing",
        ),
        notes="Similar to R01 but specifically about pending deposits",
    ),

    # =========================================================================
    # R10-R16: Processing / Administrative
    # =========================================================================
    "R10": ReturnCodeInfo(
        code="R10",
        description="Customer Advises Originator is Not Known / Not Authorized",
        fault_prior="mixed",
        ambiguity="high",  # Disputed, needs investigation
        confidence_ceiling=0.45,
        category="authorization",
        recommended_actions=(
            "Investigate immediately",
            "Review authorization documentation",
            "Potential fraud indicator",
            "Document thoroughly",
        ),
        notes="High-risk code - could indicate fraud or identity issues",
    ),
    "R11": ReturnCodeInfo(
        code="R11",
        description="Check Truncation Entry Return",
        fault_prior="provider",
        ambiguity="low",
        confidence_ceiling=0.70,
        category="processing",
        recommended_actions=(
            "Contact provider about check processing",
        ),
    ),
    "R12": ReturnCodeInfo(
        code="R12",
        description="Account Sold to Another DFI",
        fault_prior="unknown",
        ambiguity="medium",
        confidence_ceiling=0.50,
        category="administrative",
        recommended_actions=(
            "Request updated routing/account info",
            "Common during bank acquisitions",
        ),
        notes="No one at fault - bank merger/acquisition",
    ),
    "R13": ReturnCodeInfo(
        code="R13",
        description="Invalid ACH Routing Number",
        fault_prior="employer",
        ambiguity="low",
        confidence_ceiling=0.75,
        category="processing",
        recommended_actions=(
            "Verify routing number",
            "Check for data entry errors",
            "Validate against Federal Reserve directory",
        ),
        notes="Usually a data entry error when setting up payee",
    ),
    "R14": ReturnCodeInfo(
        code="R14",
        description="Representative Payee Deceased or Unable to Continue",
        fault_prior="unknown",
        ambiguity="low",
        confidence_ceiling=0.60,
        category="administrative",
        recommended_actions=(
            "Update payee records",
            "Follow deceased account procedures",
        ),
    ),
    "R15": ReturnCodeInfo(
        code="R15",
        description="Beneficiary or Account Holder Deceased",
        fault_prior="unknown",
        ambiguity="low",
        confidence_ceiling=0.60,
        category="administrative",
        recommended_actions=(
            "Update employee records",
            "Follow deceased employee procedures",
            "Route to HR",
        ),
    ),
    "R16": ReturnCodeInfo(
        code="R16",
        description="Account Frozen",
        fault_prior="employee",
        ambiguity="medium",
        confidence_ceiling=0.55,
        category="account",
        recommended_actions=(
            "Cannot retry until resolved",
            "Employee must resolve with their bank",
            "Possible legal/garnishment issue",
        ),
        notes="Account freeze could be for many reasons - needs context",
    ),

    # =========================================================================
    # R17-R24: Bank/Provider Issues
    # =========================================================================
    "R17": ReturnCodeInfo(
        code="R17",
        description="File Record Edit Criteria",
        fault_prior="psp",
        ambiguity="low",
        confidence_ceiling=0.75,
        category="processing",
        recommended_actions=(
            "Review file formatting",
            "Check field specifications",
            "Contact ACH processor",
        ),
        notes="Usually a file formatting issue on our side",
    ),
    "R18": ReturnCodeInfo(
        code="R18",
        description="Improper Effective Entry Date",
        fault_prior="psp",
        ambiguity="low",
        confidence_ceiling=0.80,
        category="processing",
        recommended_actions=(
            "Review settlement date logic",
            "Check for timezone issues",
        ),
    ),
    "R19": ReturnCodeInfo(
        code="R19",
        description="Amount Field Error",
        fault_prior="psp",
        ambiguity="low",
        confidence_ceiling=0.80,
        category="processing",
        recommended_actions=(
            "Review amount formatting",
            "Check decimal handling",
        ),
    ),
    "R20": ReturnCodeInfo(
        code="R20",
        description="Non-Transaction Account",
        fault_prior="employee",
        ambiguity="medium",
        confidence_ceiling=0.65,
        category="account",
        recommended_actions=(
            "Account type doesn't accept ACH",
            "Request different account type",
            "Verify account details with employee",
        ),
    ),
    "R21": ReturnCodeInfo(
        code="R21",
        description="Invalid Company Identification",
        fault_prior="employer",
        ambiguity="low",
        confidence_ceiling=0.75,
        category="processing",
        recommended_actions=(
            "Verify company ID with bank",
            "Check ACH setup",
        ),
    ),
    "R22": ReturnCodeInfo(
        code="R22",
        description="Invalid Individual ID Number",
        fault_prior="employer",
        ambiguity="medium",
        confidence_ceiling=0.65,
        category="processing",
        recommended_actions=(
            "Verify employee ID formatting",
            "Check data entry",
        ),
    ),
    "R23": ReturnCodeInfo(
        code="R23",
        description="Credit Entry Refused by Receiver",
        fault_prior="employee",
        ambiguity="high",
        confidence_ceiling=0.50,
        category="authorization",
        recommended_actions=(
            "Contact employee",
            "Verify they want direct deposit",
            "Check for disputes",
        ),
        notes="Employee or their bank actively refused - investigate why",
    ),
    "R24": ReturnCodeInfo(
        code="R24",
        description="Duplicate Entry",
        fault_prior="psp",
        ambiguity="low",
        confidence_ceiling=0.80,
        category="processing",
        recommended_actions=(
            "Review idempotency controls",
            "Check for system issues",
            "Verify single payment processed",
        ),
        notes="Should be caught by our idempotency - investigate if seen",
    ),

    # =========================================================================
    # R29+: Additional Codes
    # =========================================================================
    "R29": ReturnCodeInfo(
        code="R29",
        description="Corporate Customer Advises Not Authorized",
        fault_prior="mixed",
        ambiguity="high",
        confidence_ceiling=0.45,
        category="authorization",
        recommended_actions=(
            "Review corporate authorization",
            "Verify signer authority",
            "Document thoroughly",
            "Investigate potential fraud",
        ),
        notes="Similar to R10 but for corporate accounts",
    ),
    "R31": ReturnCodeInfo(
        code="R31",
        description="Permissible Return Entry (CCD and CTX only)",
        fault_prior="provider",
        ambiguity="medium",
        confidence_ceiling=0.55,
        category="administrative",
        recommended_actions=(
            "Review specific circumstances",
            "May be routine correction",
        ),
    ),
}


def get_return_code_info(code: str) -> ReturnCodeInfo:
    """
    Get information about a return code.

    Args:
        code: The return code (e.g., "R01")

    Returns:
        ReturnCodeInfo for the code, or default "unknown" info
    """
    code = code.upper().strip()

    if code in RETURN_CODE_REFERENCE:
        return RETURN_CODE_REFERENCE[code]

    # Unknown code - return conservative defaults
    return ReturnCodeInfo(
        code=code,
        description=f"Unknown return code: {code}",
        fault_prior="unknown",
        ambiguity="high",
        confidence_ceiling=0.40,
        category="other",
        recommended_actions=(
            "Investigate this return code",
            "Contact ACH processor for details",
            "Document findings",
        ),
        notes="Code not in standard NACHA reference",
    )


def get_ambiguity_confidence_penalty(ambiguity: str) -> float:
    """
    Get confidence penalty for ambiguity level.

    Higher ambiguity = larger penalty to prevent overconfidence.
    """
    return {
        "low": 0.0,
        "medium": 0.10,
        "high": 0.25,
    }.get(ambiguity, 0.30)


def get_all_codes_by_fault_prior(fault_prior: str) -> list[str]:
    """Get all codes with a given fault prior."""
    return [
        code for code, info in RETURN_CODE_REFERENCE.items()
        if info.fault_prior == fault_prior
    ]


def get_high_ambiguity_codes() -> list[str]:
    """Get codes that require investigation (high ambiguity)."""
    return [
        code for code, info in RETURN_CODE_REFERENCE.items()
        if info.ambiguity == "high"
    ]
