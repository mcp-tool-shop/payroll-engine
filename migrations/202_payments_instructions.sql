-- 202_payments_instructions.sql (FULL)
-- Payment intent/attempt model; settlement truth is in psp_settlement_event.

BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS payment_instruction (
  payment_instruction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('employee_net','tax_remit','third_party','refund','fee','funding_debit')),
  direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  payee_type TEXT NOT NULL CHECK (payee_type IN ('employee','agency','provider','client')),
  payee_ref_id UUID NOT NULL,
  requested_settlement_date DATE,
  status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','queued','submitted','accepted','settled','failed','reversed','canceled')),
  idempotency_key TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id UUID NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS payment_instruction_status
  ON payment_instruction(tenant_id, legal_entity_id, status, requested_settlement_date);

CREATE TABLE IF NOT EXISTS payment_attempt (
  payment_attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_instruction_id UUID NOT NULL REFERENCES payment_instruction(payment_instruction_id),
  rail TEXT NOT NULL CHECK (rail IN ('ach','wire','rtp','fednow','check')),
  provider TEXT NOT NULL,
  provider_request_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('submitted','accepted','failed')),
  request_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS payment_attempt_provider_uq
  ON payment_attempt(provider, provider_request_id);

COMMIT;
