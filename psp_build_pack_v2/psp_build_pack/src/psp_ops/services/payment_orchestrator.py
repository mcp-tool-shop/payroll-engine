from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
from .ledger_service import LedgerService
from ..providers.base import PaymentRailProvider

class PaymentOrchestrator:
    def __init__(self, db: Session, ledger: LedgerService, provider: PaymentRailProvider):
        self.db = db
        self.ledger = ledger
        self.provider = provider

    def create_employee_net_instruction(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        employee_id: str,
        pay_statement_id: str,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date=None,
    ) -> str:
        row = self.db.execute(text("""
          INSERT INTO payment_instruction(
            tenant_id, legal_entity_id, purpose, direction, amount, currency, payee_type, payee_ref_id,
            requested_settlement_date, status, idempotency_key, source_type, source_id
          ) VALUES (
            :tenant_id, :le, 'employee_net', 'outbound', :amount, 'USD', 'employee', :employee_id,
            :rsd, 'created', :idk, 'pay_statement', :psid
          )
          ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
          RETURNING payment_instruction_id
        """), {
            "tenant_id": tenant_id,
            "le": legal_entity_id,
            "amount": str(amount),
            "employee_id": employee_id,
            "rsd": requested_settlement_date,
            "idk": idempotency_key,
            "psid": pay_statement_id,
        }).fetchone()

        if row and row[0]:
            return str(row[0])

        existing = self.db.execute(text("""
          SELECT payment_instruction_id FROM payment_instruction
          WHERE tenant_id=:tenant_id AND idempotency_key=:idk
        """), {"tenant_id": tenant_id, "idk": idempotency_key}).fetchone()
        if not existing:
            raise RuntimeError("Failed to create payment_instruction")
        return str(existing[0])

    def submit(self, *, tenant_id: str, payment_instruction_id: str) -> str:
        instr = self.db.execute(text("""
          SELECT payment_instruction_id, amount, idempotency_key, purpose, payee_type, payee_ref_id, tenant_id, legal_entity_id
          FROM payment_instruction
          WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
        """), {"id": payment_instruction_id, "tenant_id": tenant_id}).fetchone()
        if not instr:
            raise ValueError("payment_instruction not found")

        instruction_payload = {
            "payment_instruction_id": str(instr[0]),
            "amount": str(instr[1]),
            "idempotency_key": instr[2],
            "purpose": instr[3],
            "payee_type": instr[4],
            "payee_ref_id": str(instr[5]),
        }
        submit = self.provider.submit(instruction_payload)

        # record attempt (idempotent by provider_request_id)
        self.db.execute(text("""
          INSERT INTO payment_attempt(payment_instruction_id, rail, provider, provider_request_id, status, request_payload_json)
          VALUES (:pi, :rail, :provider, :req, :status, :payload::jsonb)
          ON CONFLICT (provider, provider_request_id) DO NOTHING
        """), {
            "pi": str(instr[0]),
            "rail": "ach" if "ach" in self.provider.provider_name else "fednow",
            "provider": self.provider.provider_name,
            "req": submit.provider_request_id,
            "status": "accepted" if submit.accepted else "failed",
            "payload": instruction_payload,
        })

        # update instruction status
        self.db.execute(text("""
          UPDATE payment_instruction
          SET status = :st
          WHERE payment_instruction_id = :id
        """), {"st": "accepted" if submit.accepted else "failed", "id": str(instr[0])})

        return submit.provider_request_id
