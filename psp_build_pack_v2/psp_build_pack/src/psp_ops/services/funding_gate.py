from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

@dataclass(frozen=True)
class GateResult:
    outcome: str  # pass/soft_fail/hard_fail
    required_amount: Decimal
    available_amount: Decimal
    reasons: list[dict]

class FundingGateService:
    def __init__(self, db: Session):
        self.db = db

    def evaluate_commit_gate(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        pay_run_id: str,
        funding_model: str,
        idempotency_key: str,
        strict: bool = True,
    ) -> GateResult:
        # Compute required funding from committed (or preview) payroll outputs.
        # NOTE: Adapt these queries to your schema naming. This is a minimal contract.
        totals = self.db.execute(text("""
          SELECT
            COALESCE(SUM(ps.net_pay),0) AS total_net
          FROM pay_statement ps
          JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
          WHERE pre.pay_run_id = :pay_run_id
        """), {"pay_run_id": pay_run_id}).fetchone()

        required = Decimal(str(totals[0] if totals else 0))
        # TODO: add taxes and third-party amounts based on funding_model
        # For now, require at least net pay to exist.

        # Available: sum of balances in client_funding_clearing for this legal entity
        # Simplified: compute from ledger entries (production should snapshot)
        available = self.db.execute(text("""
          WITH acct AS (
            SELECT psp_ledger_account_id
            FROM psp_ledger_account
            WHERE tenant_id=:tenant_id AND legal_entity_id=:le AND account_type='client_funding_clearing'
            LIMIT 1
          ),
          credits AS (
            SELECT COALESCE(SUM(amount),0) AS c FROM psp_ledger_entry e, acct
            WHERE e.tenant_id=:tenant_id AND e.credit_account_id = acct.psp_ledger_account_id
          ),
          debits AS (
            SELECT COALESCE(SUM(amount),0) AS d FROM psp_ledger_entry e, acct
            WHERE e.tenant_id=:tenant_id AND e.debit_account_id = acct.psp_ledger_account_id
          )
          SELECT (credits.c - debits.d) FROM credits, debits
        """), {"tenant_id": tenant_id, "le": legal_entity_id}).scalar()
        available = Decimal(str(available or 0))

        reasons = []
        if available < required:
            reasons.append({
                "code": "INSUFFICIENT_FUNDS",
                "message": f"Funding not received. Expected {required} USD, available {available} USD."
            })

        outcome = "pass" if not reasons else ("hard_fail" if strict else "soft_fail")

        # persist evaluation (idempotent)
        self.db.execute(text("""
          INSERT INTO funding_gate_evaluation(
            tenant_id, legal_entity_id, pay_run_id, gate_type, outcome, required_amount, available_amount, reasons_json, idempotency_key
          ) VALUES (
            :tenant_id, :le, :pay_run_id, 'commit_gate', :outcome, :required, :available, :reasons::jsonb, :idk
          )
          ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        """), {
            "tenant_id": tenant_id,
            "le": legal_entity_id,
            "pay_run_id": pay_run_id,
            "outcome": outcome,
            "required": str(required),
            "available": str(available),
            "reasons": reasons,
            "idk": idempotency_key,
        })

        return GateResult(outcome=outcome, required_amount=required, available_amount=available, reasons=reasons)
