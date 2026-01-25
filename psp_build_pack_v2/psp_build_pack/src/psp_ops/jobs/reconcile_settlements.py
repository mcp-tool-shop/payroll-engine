from __future__ import annotations

import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..providers.base import PaymentRailProvider

def reconcile_provider(db: Session, provider: PaymentRailProvider, *, psp_bank_account_id: str, date: datetime.date):
    """Pull settlement records from a provider and upsert into psp_settlement_event.
    Idempotent on (psp_bank_account_id, external_trace_id).
    """
    records = provider.reconcile(date)
    for r in records:
        db.execute(text("""
          INSERT INTO psp_settlement_event(
            psp_bank_account_id, rail, direction, amount, currency, status, external_trace_id, effective_date, raw_payload_json
          ) VALUES (
            :bank, :rail, :dir, :amt, :cur, :st, :trace, :eff, :raw::jsonb
          )
          ON CONFLICT (psp_bank_account_id, external_trace_id) DO UPDATE
          SET status = EXCLUDED.status,
              effective_date = COALESCE(EXCLUDED.effective_date, psp_settlement_event.effective_date),
              raw_payload_json = psp_settlement_event.raw_payload_json || EXCLUDED.raw_payload_json
        """), {
            "bank": psp_bank_account_id,
            "rail": "ach" if provider.capabilities().ach_credit or provider.capabilities().ach_debit else "fednow",
            "dir": "inbound" if provider.capabilities().ach_debit else "outbound",
            "amt": r.amount,
            "cur": r.currency,
            "st": r.status,
            "trace": r.external_trace_id,
            "eff": r.effective_date,
            "raw": r.raw_payload or {},
        })
