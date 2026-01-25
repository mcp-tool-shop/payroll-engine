"""
AI-Assisted Runbook Execution.

Given an incident (e.g., settlement mismatch), the advisor can:
- Identify the relevant runbook
- Pre-fill the diagnostic queries
- Summarize likely causes
- Propose next checks

But it MUST STOP at: recommendation + evidence.
It NEVER takes action automatically.

CRITICAL: This is advisory-only. It NEVER modifies state.

SECURITY GUARANTEE - NO SQL EXECUTION:
    The assistant ONLY suggests queries. It NEVER runs them.
    All DiagnosticQuery objects contain SQL text but execution
    is the responsibility of the operator using their own
    authorized database connection.

    This separation ensures:
    - No privilege escalation via AI
    - Full audit trail of operator actions
    - AI cannot exfiltrate data
    - AI cannot modify financial state
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


class IncidentType(Enum):
    """Types of incidents the assistant can help with."""
    SETTLEMENT_MISMATCH = "settlement_mismatch"
    FUNDING_BLOCK = "funding_block"
    PAYMENT_RETURN = "payment_return"
    REVERSAL_DISPUTE = "reversal_dispute"
    LEDGER_IMBALANCE = "ledger_imbalance"
    DELAYED_SETTLEMENT = "delayed_settlement"
    DUPLICATE_PAYMENT = "duplicate_payment"
    UNKNOWN = "unknown"


class DiagnosticStatus(Enum):
    """Status of a diagnostic check."""
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    NEEDS_DATA = "needs_data"


@dataclass(frozen=True)
class DiagnosticQuery:
    """
    A pre-filled diagnostic query for investigation.

    SECURITY: The assistant generates these but NEVER executes them.
    Execution is the operator's responsibility using their own
    authorized database connection. This is a deliberate security
    boundary - AI cannot access or modify data directly.
    """
    query_id: str
    name: str
    description: str
    query_sql: str  # SQL text only - NOT executed by this module
    expected_outcome: str
    if_anomalous: str  # What to do if result is unexpected

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "query_id": self.query_id,
            "name": self.name,
            "description": self.description,
            "query_sql": self.query_sql,
            "expected_outcome": self.expected_outcome,
            "if_anomalous": self.if_anomalous,
        }


@dataclass(frozen=True)
class LikelyCause:
    """A potential cause for the incident."""
    cause: str
    probability: str  # "high", "medium", "low"
    evidence_for: list[str]
    evidence_against: list[str]
    next_steps: list[str]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "cause": self.cause,
            "probability": self.probability,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "next_steps": self.next_steps,
        }


@dataclass(frozen=True)
class RunbookStep:
    """A step from the relevant runbook."""
    step_number: int
    action: str
    details: str
    is_completed: bool = False
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "step_number": self.step_number,
            "action": self.action,
            "details": self.details,
            "is_completed": self.is_completed,
            "notes": self.notes,
        }


@dataclass
class IncidentContext:
    """
    Context about an incident for the assistant.

    This is input to the runbook assistant.
    """
    incident_id: UUID
    incident_type: IncidentType
    detected_at: datetime
    tenant_id: UUID

    # Incident details (varies by type)
    amount: Optional[Decimal] = None
    payment_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    return_code: Optional[str] = None
    mismatch_amount: Optional[Decimal] = None

    # Additional context
    description: str = ""
    severity: str = "unknown"  # low, medium, high, critical

    # Related data
    related_events: list[dict] = field(default_factory=list)
    related_payments: list[dict] = field(default_factory=list)


@dataclass
class RunbookAssistance:
    """
    AI-generated runbook assistance.

    This is the output of the assistant - it provides
    guidance but NEVER takes action.
    """
    assistance_id: UUID
    incident_id: UUID
    incident_type: IncidentType
    generated_at: datetime

    # Runbook identification
    runbook_name: str
    runbook_path: str
    runbook_summary: str

    # Diagnostic queries (pre-filled but NOT executed)
    diagnostic_queries: list[DiagnosticQuery] = field(default_factory=list)

    # Analysis
    likely_causes: list[LikelyCause] = field(default_factory=list)
    recommended_steps: list[RunbookStep] = field(default_factory=list)

    # Quick summary
    summary: str = ""
    estimated_severity: str = "unknown"
    estimated_resolution_time: str = "unknown"

    # Warnings
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "assistance_id": str(self.assistance_id),
            "incident_id": str(self.incident_id),
            "incident_type": self.incident_type.value,
            "generated_at": self.generated_at.isoformat(),
            "runbook": {
                "name": self.runbook_name,
                "path": self.runbook_path,
                "summary": self.runbook_summary,
            },
            "diagnostic_queries": [q.to_dict() for q in self.diagnostic_queries],
            "likely_causes": [c.to_dict() for c in self.likely_causes],
            "recommended_steps": [s.to_dict() for s in self.recommended_steps],
            "summary": self.summary,
            "estimated_severity": self.estimated_severity,
            "estimated_resolution_time": self.estimated_resolution_time,
            "warnings": self.warnings,
        }

    def to_markdown(self, max_queries: int = 10, max_causes: int = 5) -> str:
        """
        Generate markdown report.

        Args:
            max_queries: Maximum diagnostic queries to show (default 10)
            max_causes: Maximum likely causes to show (default 5)
        """
        lines = [
            f"# Runbook Assistance: {self.runbook_name}",
            f"",
            f"**Incident ID:** {self.incident_id}",
            f"**Type:** {self.incident_type.value}",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"",
            f"## Summary",
            f"",
            f"{self.summary}",
            f"",
            f"**Estimated Severity:** {self.estimated_severity}",
            f"**Estimated Resolution:** {self.estimated_resolution_time}",
            f"",
        ]

        if self.warnings:
            lines.extend([
                f"## ⚠️ Warnings",
                f"",
            ])
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        if self.likely_causes:
            lines.extend([
                f"## Likely Causes",
                f"",
            ])
            causes_to_show = self.likely_causes[:max_causes] if max_causes > 0 else self.likely_causes
            for i, cause in enumerate(causes_to_show, 1):
                lines.extend([
                    f"### {i}. {cause.cause} ({cause.probability} probability)",
                    f"",
                    f"**Evidence for:**",
                ])
                for e in cause.evidence_for[:5]:  # Max 5 evidence items
                    lines.append(f"- [+] {e}")
                if len(cause.evidence_for) > 5:
                    lines.append(f"- ... and {len(cause.evidence_for) - 5} more")
                if cause.evidence_against:
                    lines.append(f"")
                    lines.append(f"**Evidence against:**")
                    for e in cause.evidence_against[:5]:
                        lines.append(f"- [-] {e}")
                    if len(cause.evidence_against) > 5:
                        lines.append(f"- ... and {len(cause.evidence_against) - 5} more")
                lines.append("")
            if max_causes > 0 and len(self.likely_causes) > max_causes:
                remaining = len(self.likely_causes) - max_causes
                lines.append(f"*... and {remaining} additional potential causes*")
                lines.append("")

        if self.diagnostic_queries:
            lines.extend([
                f"## Diagnostic Queries",
                f"",
                f"Run these queries to gather evidence (assistant suggests only, NEVER executes):",
                f"",
            ])
            queries_to_show = self.diagnostic_queries[:max_queries] if max_queries > 0 else self.diagnostic_queries
            for q in queries_to_show:
                lines.extend([
                    f"### {q.name}",
                    f"",
                    f"{q.description}",
                    f"",
                    f"```sql",
                    f"{q.query_sql}",
                    f"```",
                    f"",
                    f"**Expected:** {q.expected_outcome}",
                    f"",
                    f"**If anomalous:** {q.if_anomalous}",
                    f"",
                ])
            if max_queries > 0 and len(self.diagnostic_queries) > max_queries:
                remaining = len(self.diagnostic_queries) - max_queries
                lines.append(f"*... and {remaining} additional diagnostic queries*")
                lines.append("")

        if self.recommended_steps:
            lines.extend([
                f"## Recommended Steps",
                f"",
            ])
            for step in self.recommended_steps:
                checkbox = "☑" if step.is_completed else "☐"
                lines.append(f"{checkbox} **Step {step.step_number}:** {step.action}")
                lines.append(f"   {step.details}")
                lines.append("")

        lines.extend([
            f"---",
            f"",
            f"**Runbook:** `{self.runbook_path}`",
            f"",
            f"*This is AI-generated guidance. Always verify before taking action.*",
        ])

        return "\n".join(lines)


class RunbookAssistant:
    """
    AI assistant for runbook execution.

    This assistant:
    - Identifies the relevant runbook for an incident
    - Pre-fills diagnostic queries
    - Summarizes likely causes
    - Proposes next steps

    It NEVER takes action - only provides guidance.
    """

    # Runbook registry (would be loaded from docs/runbooks/ in production)
    RUNBOOKS = {
        IncidentType.SETTLEMENT_MISMATCH: {
            "name": "Settlement Mismatch",
            "path": "docs/runbooks/settlement_mismatch.md",
            "summary": "Handles cases where settled amount doesn't match expected amount",
        },
        IncidentType.FUNDING_BLOCK: {
            "name": "Funding Gate Blocks",
            "path": "docs/runbooks/funding_blocks.md",
            "summary": "Handles cases where payroll is blocked due to funding issues",
        },
        IncidentType.PAYMENT_RETURN: {
            "name": "Payment Returns",
            "path": "docs/runbooks/returns.md",
            "summary": "Handles ACH returns and their root cause analysis",
        },
        IncidentType.REVERSAL_DISPUTE: {
            "name": "Reversal Disputes",
            "path": "docs/runbooks/reversals.md",
            "summary": "Handles disputed reversals and their resolution",
        },
        IncidentType.LEDGER_IMBALANCE: {
            "name": "Ledger Imbalance",
            "path": "docs/runbooks/ledger_imbalance.md",
            "summary": "Handles cases where ledger entries don't sum to zero",
        },
        IncidentType.DELAYED_SETTLEMENT: {
            "name": "Delayed Settlement",
            "path": "docs/runbooks/delayed_settlement.md",
            "summary": "Handles settlements that haven't arrived within expected window",
        },
        IncidentType.DUPLICATE_PAYMENT: {
            "name": "Duplicate Payment",
            "path": "docs/runbooks/duplicates.md",
            "summary": "Handles potential duplicate payment detection and resolution",
        },
    }

    def assist(self, context: IncidentContext) -> RunbookAssistance:
        """
        Generate runbook assistance for an incident.

        Args:
            context: Context about the incident

        Returns:
            RunbookAssistance with guidance (NOT actions)
        """
        runbook = self.RUNBOOKS.get(context.incident_type, {
            "name": "Unknown Incident Type",
            "path": "docs/runbooks/general.md",
            "summary": "General incident investigation procedures",
        })

        assistance = RunbookAssistance(
            assistance_id=uuid4(),
            incident_id=context.incident_id,
            incident_type=context.incident_type,
            generated_at=datetime.utcnow(),
            runbook_name=runbook["name"],
            runbook_path=runbook["path"],
            runbook_summary=runbook["summary"],
        )

        # Generate type-specific guidance
        if context.incident_type == IncidentType.SETTLEMENT_MISMATCH:
            self._assist_settlement_mismatch(context, assistance)
        elif context.incident_type == IncidentType.FUNDING_BLOCK:
            self._assist_funding_block(context, assistance)
        elif context.incident_type == IncidentType.PAYMENT_RETURN:
            self._assist_payment_return(context, assistance)
        elif context.incident_type == IncidentType.LEDGER_IMBALANCE:
            self._assist_ledger_imbalance(context, assistance)
        else:
            self._assist_generic(context, assistance)

        return assistance

    def _assist_settlement_mismatch(
        self,
        context: IncidentContext,
        assistance: RunbookAssistance,
    ) -> None:
        """Generate assistance for settlement mismatch."""
        tenant_id = str(context.tenant_id)
        payment_id = str(context.payment_id) if context.payment_id else "?"
        amount = context.amount or Decimal("0")
        mismatch = context.mismatch_amount or Decimal("0")

        assistance.summary = (
            f"Settlement mismatch detected: expected ${amount:,.2f}, "
            f"difference of ${mismatch:,.2f}. "
            f"This requires investigation to determine if it's a timing issue, "
            f"provider error, or data discrepancy."
        )

        assistance.estimated_severity = "high" if abs(mismatch) > 1000 else "medium"
        assistance.estimated_resolution_time = "1-4 hours"

        # Diagnostic queries
        assistance.diagnostic_queries = [
            DiagnosticQuery(
                query_id="sm_1",
                name="Check settlement events",
                description="Find all settlement events for this payment",
                query_sql=f"""
SELECT event_type, payload, recorded_at
FROM psp_events
WHERE tenant_id = '{tenant_id}'
  AND payload->>'payment_id' = '{payment_id}'
  AND event_type LIKE '%Settlement%'
ORDER BY recorded_at;
""".strip(),
                expected_outcome="Single SettlementReceived event matching expected amount",
                if_anomalous="Multiple events may indicate duplicate settlement or partial settlement",
            ),
            DiagnosticQuery(
                query_id="sm_2",
                name="Check provider settlement report",
                description="Compare with provider's reported settlement",
                query_sql=f"""
SELECT *
FROM provider_settlement_reports
WHERE payment_reference = '{payment_id}'
  AND report_date >= CURRENT_DATE - INTERVAL '7 days';
""".strip(),
                expected_outcome="Provider report matches our records",
                if_anomalous="Mismatch may indicate provider reporting error or timing issue",
            ),
            DiagnosticQuery(
                query_id="sm_3",
                name="Check for returns",
                description="Check if a return was received that reduced settlement",
                query_sql=f"""
SELECT *
FROM psp_events
WHERE tenant_id = '{tenant_id}'
  AND event_type = 'ReturnReceived'
  AND payload->>'original_payment_id' = '{payment_id}';
""".strip(),
                expected_outcome="No returns, or returns that explain the mismatch",
                if_anomalous="Unexpected return may explain mismatch",
            ),
        ]

        # Likely causes
        assistance.likely_causes = [
            LikelyCause(
                cause="Partial return processed",
                probability="high",
                evidence_for=[
                    "Mismatch is less than original amount",
                    "Payment was to employee account (ACH)",
                ],
                evidence_against=[
                    "No return events in event store",
                ],
                next_steps=[
                    "Run query sm_3 to check for returns",
                    "Check provider portal for return notifications",
                ],
            ),
            LikelyCause(
                cause="Provider reporting delay",
                probability="medium",
                evidence_for=[
                    "Settlement is recent (within 3 days)",
                    "Provider has had reporting issues before",
                ],
                evidence_against=[
                    "Settlement is older than 5 days",
                ],
                next_steps=[
                    "Wait 24-48 hours and re-check",
                    "Contact provider if persists",
                ],
            ),
            LikelyCause(
                cause="Duplicate settlement entry",
                probability="low",
                evidence_for=[],
                evidence_against=[
                    "Our idempotency controls should prevent this",
                ],
                next_steps=[
                    "Run query sm_1 to check for duplicates",
                    "Review event deduplication logs",
                ],
            ),
        ]

        # Recommended steps
        assistance.recommended_steps = [
            RunbookStep(1, "Verify mismatch amount", "Confirm the exact difference and direction"),
            RunbookStep(2, "Run diagnostic queries", "Execute queries sm_1, sm_2, sm_3"),
            RunbookStep(3, "Check for returns", "Look for any returns that may explain difference"),
            RunbookStep(4, "Review provider data", "Compare with provider settlement report"),
            RunbookStep(5, "Document findings", "Record root cause in incident log"),
            RunbookStep(6, "Apply correction if needed", "Create adjustment entry if verified"),
        ]

        assistance.warnings = [
            "Do NOT create correction entries until root cause is confirmed",
            "Settlement timing varies by provider - wait 48h before escalating",
        ]

    def _assist_funding_block(
        self,
        context: IncidentContext,
        assistance: RunbookAssistance,
    ) -> None:
        """Generate assistance for funding block."""
        tenant_id = str(context.tenant_id)
        batch_id = str(context.batch_id) if context.batch_id else "?"
        amount = context.amount or Decimal("0")

        assistance.summary = (
            f"Payroll batch blocked due to funding gate failure. "
            f"Amount: ${amount:,.2f}. "
            f"This prevents payroll execution until funding is verified."
        )

        assistance.estimated_severity = "high"
        assistance.estimated_resolution_time = "1-2 hours (depends on funding resolution)"

        assistance.diagnostic_queries = [
            DiagnosticQuery(
                query_id="fb_1",
                name="Check funding gate result",
                description="Find the funding gate evaluation that caused the block",
                query_sql=f"""
SELECT event_type, payload, recorded_at
FROM psp_events
WHERE tenant_id = '{tenant_id}'
  AND event_type = 'FundingGateBlocked'
  AND payload->>'batch_id' = '{batch_id}'
ORDER BY recorded_at DESC
LIMIT 1;
""".strip(),
                expected_outcome="Single block event with clear reason",
                if_anomalous="Multiple blocks may indicate repeated attempts",
            ),
            DiagnosticQuery(
                query_id="fb_2",
                name="Check current balance",
                description="Verify tenant's current available balance",
                query_sql=f"""
SELECT
    balance_type,
    amount,
    updated_at
FROM tenant_balances
WHERE tenant_id = '{tenant_id}'
ORDER BY balance_type;
""".strip(),
                expected_outcome="Available balance should be >= payroll amount",
                if_anomalous="Insufficient balance confirms the block reason",
            ),
            DiagnosticQuery(
                query_id="fb_3",
                name="Check pending settlements",
                description="Look for incoming settlements that might clear",
                query_sql=f"""
SELECT
    payment_id,
    amount,
    expected_settlement_date,
    status
FROM pending_settlements
WHERE tenant_id = '{tenant_id}'
  AND status = 'pending'
ORDER BY expected_settlement_date;
""".strip(),
                expected_outcome="Pending settlements that may cover shortfall",
                if_anomalous="No pending settlements means external funding needed",
            ),
        ]

        assistance.likely_causes = [
            LikelyCause(
                cause="Insufficient available balance",
                probability="high",
                evidence_for=[
                    "Payroll amount exceeds current balance",
                    "Recent large payroll depleted funds",
                ],
                evidence_against=[],
                next_steps=[
                    "Run query fb_2 to confirm balance",
                    "Request additional funding from tenant",
                ],
            ),
            LikelyCause(
                cause="Pending settlements not yet cleared",
                probability="medium",
                evidence_for=[
                    "Balance was sufficient yesterday",
                    "Settlements expected soon",
                ],
                evidence_against=[
                    "No pending settlements exist",
                ],
                next_steps=[
                    "Run query fb_3 to check pending",
                    "Consider waiting for settlement if close",
                ],
            ),
        ]

        assistance.recommended_steps = [
            RunbookStep(1, "Identify block reason", "Review FundingGateBlocked event payload"),
            RunbookStep(2, "Verify current balance", "Run query fb_2"),
            RunbookStep(3, "Check pending settlements", "Run query fb_3"),
            RunbookStep(4, "Contact tenant if needed", "Request additional funding"),
            RunbookStep(5, "Re-evaluate gate", "Once funding resolved, retry the gate"),
        ]

        assistance.warnings = [
            "Do NOT bypass funding gate - this protects against loss",
            "Verify funding source before proceeding",
        ]

    def _assist_payment_return(
        self,
        context: IncidentContext,
        assistance: RunbookAssistance,
    ) -> None:
        """Generate assistance for payment return."""
        tenant_id = str(context.tenant_id)
        payment_id = str(context.payment_id) if context.payment_id else "?"
        return_code = context.return_code or "UNKNOWN"
        amount = context.amount or Decimal("0")

        assistance.summary = (
            f"Payment return received with code {return_code}. "
            f"Amount: ${amount:,.2f}. "
            f"Root cause analysis required for liability classification."
        )

        # Severity depends on return code
        if return_code in ("R10", "R29"):  # Fraud indicators
            assistance.estimated_severity = "critical"
        elif return_code in ("R01", "R02", "R03"):  # Account issues
            assistance.estimated_severity = "medium"
        else:
            assistance.estimated_severity = "low"

        assistance.estimated_resolution_time = "30 minutes - 2 hours"

        assistance.diagnostic_queries = [
            DiagnosticQuery(
                query_id="pr_1",
                name="Check AI advisory",
                description="Get AI-generated root cause advisory",
                query_sql=f"""
SELECT *
FROM psp_events
WHERE tenant_id = '{tenant_id}'
  AND event_type = 'ReturnAdvisoryGenerated'
  AND payload->>'payment_id' = '{payment_id}'
ORDER BY recorded_at DESC
LIMIT 1;
""".strip(),
                expected_outcome="Advisory with suggested error origin and confidence",
                if_anomalous="No advisory may indicate AI was disabled or event timing issue",
            ),
            DiagnosticQuery(
                query_id="pr_2",
                name="Check payee history",
                description="Look for patterns with this payee",
                query_sql=f"""
SELECT
    COUNT(*) AS return_count,
    array_agg(DISTINCT payload->>'return_code') AS return_codes
FROM psp_events
WHERE tenant_id = '{tenant_id}'
  AND event_type = 'ReturnReceived'
  AND payload->>'payee_id' = (
      SELECT payload->>'payee_id'
      FROM psp_events
      WHERE event_type = 'PaymentInstructed'
        AND payload->>'payment_id' = '{payment_id}'
  )
  AND recorded_at >= NOW() - INTERVAL '90 days';
""".strip(),
                expected_outcome="Low return count indicates isolated incident",
                if_anomalous="High return count suggests chronic payee issue",
            ),
        ]

        # Return code specific guidance
        from payroll_engine.psp.ai.return_codes import get_return_code_info
        code_info = get_return_code_info(return_code)

        assistance.likely_causes = [
            LikelyCause(
                cause=f"{code_info.fault_prior.title()} fault ({code_info.description})",
                probability="high" if code_info.ambiguity == "low" else "medium",
                evidence_for=[
                    f"Return code {return_code} typically indicates {code_info.fault_prior} fault",
                ],
                evidence_against=[
                    "Context may reveal different cause",
                ] if code_info.ambiguity != "low" else [],
                next_steps=list(code_info.recommended_actions),
            ),
        ]

        assistance.recommended_steps = [
            RunbookStep(1, "Review AI advisory", "Check suggested error origin and confidence"),
            RunbookStep(2, "Check payee history", "Look for patterns indicating chronic issues"),
            RunbookStep(3, "Classify liability", "Assign error origin and liability party"),
            RunbookStep(4, "Initiate recovery", "Follow recovery path based on classification"),
        ]

        if code_info.ambiguity == "high":
            assistance.warnings.append(
                f"Return code {return_code} is highly ambiguous - manual investigation required"
            )

    def _assist_ledger_imbalance(
        self,
        context: IncidentContext,
        assistance: RunbookAssistance,
    ) -> None:
        """Generate assistance for ledger imbalance."""
        tenant_id = str(context.tenant_id)
        mismatch = context.mismatch_amount or Decimal("0")

        assistance.summary = (
            f"Ledger imbalance detected: entries do not sum to zero. "
            f"Imbalance: ${mismatch:,.2f}. "
            f"This is a CRITICAL invariant violation that requires immediate investigation."
        )

        assistance.estimated_severity = "critical"
        assistance.estimated_resolution_time = "2-8 hours"

        assistance.diagnostic_queries = [
            DiagnosticQuery(
                query_id="li_1",
                name="Calculate ledger totals",
                description="Sum all ledger entries by type",
                query_sql=f"""
SELECT
    entry_type,
    SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) AS total_debits,
    SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END) AS total_credits,
    SUM(CASE WHEN direction = 'debit' THEN amount ELSE -amount END) AS net
FROM ledger_entries
WHERE tenant_id = '{tenant_id}'
GROUP BY entry_type
ORDER BY entry_type;
""".strip(),
                expected_outcome="All entry types should balance to zero",
                if_anomalous="Imbalanced entry type indicates missing counter-entry",
            ),
            DiagnosticQuery(
                query_id="li_2",
                name="Find recent entries",
                description="Look at most recent ledger activity",
                query_sql=f"""
SELECT
    entry_id,
    entry_type,
    direction,
    amount,
    reference_id,
    created_at
FROM ledger_entries
WHERE tenant_id = '{tenant_id}'
ORDER BY created_at DESC
LIMIT 20;
""".strip(),
                expected_outcome="Entries should come in balanced pairs",
                if_anomalous="Single entry without matching pair indicates bug",
            ),
        ]

        assistance.likely_causes = [
            LikelyCause(
                cause="Missing counter-entry from interrupted transaction",
                probability="high",
                evidence_for=[
                    "Ledger entries are written transactionally",
                    "Imbalance suggests incomplete transaction",
                ],
                evidence_against=[],
                next_steps=[
                    "Check for failed transactions around imbalance time",
                    "Review application logs for errors",
                ],
            ),
            LikelyCause(
                cause="Bug in ledger entry code",
                probability="medium",
                evidence_for=[
                    "Imbalance is consistent pattern",
                ],
                evidence_against=[
                    "Ledger code has invariant checks",
                ],
                next_steps=[
                    "Review recent code changes to ledger module",
                    "Check for bypassed invariant checks",
                ],
            ),
        ]

        assistance.recommended_steps = [
            RunbookStep(1, "STOP all operations", "Prevent further entries until resolved"),
            RunbookStep(2, "Calculate exact imbalance", "Run query li_1"),
            RunbookStep(3, "Find the breaking entry", "Identify which transaction caused imbalance"),
            RunbookStep(4, "Review transaction logs", "Check for errors or timeouts"),
            RunbookStep(5, "Create correction entry", "ONLY after root cause confirmed"),
            RunbookStep(6, "Add monitoring", "Prevent recurrence"),
        ]

        assistance.warnings = [
            "CRITICAL: Ledger imbalance is an invariant violation",
            "Do NOT create correction entries without understanding root cause",
            "This may indicate a serious bug - escalate immediately",
        ]

    def _assist_generic(
        self,
        context: IncidentContext,
        assistance: RunbookAssistance,
    ) -> None:
        """Generate generic assistance for unknown incident types."""
        assistance.summary = (
            f"Incident detected requiring investigation. "
            f"Type: {context.incident_type.value}. "
            f"Follow general investigation procedures."
        )

        assistance.estimated_severity = context.severity
        assistance.estimated_resolution_time = "varies"

        assistance.recommended_steps = [
            RunbookStep(1, "Gather context", "Collect all relevant information about the incident"),
            RunbookStep(2, "Review event history", "Check event store for related events"),
            RunbookStep(3, "Check AI advisories", "Review any AI-generated guidance"),
            RunbookStep(4, "Identify impact", "Determine scope and affected parties"),
            RunbookStep(5, "Document findings", "Record investigation results"),
            RunbookStep(6, "Resolve or escalate", "Fix if possible, escalate if not"),
        ]

        assistance.warnings = [
            "Unknown incident type - proceed with caution",
            "Escalate if unable to determine appropriate actions",
        ]


def create_assistance_event(assistance: RunbookAssistance) -> dict:
    """
    Create a RunbookAssistanceGenerated event.

    This event enables tracking of AI assistance usage.
    """
    return {
        "event_type": "RunbookAssistanceGenerated",
        "event_id": str(uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "payload": {
            "assistance_id": str(assistance.assistance_id),
            "incident_id": str(assistance.incident_id),
            "incident_type": assistance.incident_type.value,
            "runbook_name": assistance.runbook_name,
            "query_count": len(assistance.diagnostic_queries),
            "cause_count": len(assistance.likely_causes),
            "step_count": len(assistance.recommended_steps),
            "estimated_severity": assistance.estimated_severity,
        },
    }
