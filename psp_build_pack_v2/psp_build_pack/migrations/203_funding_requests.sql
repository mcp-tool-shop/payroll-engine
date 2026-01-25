-- 203_funding_requests.sql (FULL)
-- Funding requests (client -> PSP) and events, plus funding gate evaluation.

BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS funding_request (
  funding_request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  funding_model TEXT NOT NULL CHECK (funding_model IN ('prefund_all','net_only','net_and_third_party','split_schedule')),
  rail TEXT NOT NULL CHECK (rail IN ('ach','wire','rtp','fednow')),
  direction TEXT NOT NULL DEFAULT 'inbound' CHECK (direction IN ('inbound')),
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  requested_settlement_date DATE,
  status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','submitted','accepted','settled','failed','returned','canceled')),
  idempotency_key TEXT NOT NULL,
  source_type TEXT NOT NULL, -- typically pay_run
  source_id UUID NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS funding_request_status
  ON funding_request(tenant_id, legal_entity_id, status, requested_settlement_date);

CREATE TABLE IF NOT EXISTS funding_event (
  funding_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  funding_request_id UUID NOT NULL REFERENCES funding_request(funding_request_id),
  status TEXT NOT NULL CHECK (status IN ('submitted','accepted','settled','failed','returned')),
  external_trace_id TEXT NOT NULL,
  effective_date DATE,
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (funding_request_id, external_trace_id)
);

CREATE TABLE IF NOT EXISTS funding_gate_evaluation (
  funding_gate_evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  pay_run_id UUID NOT NULL,
  gate_type TEXT NOT NULL CHECK (gate_type IN ('commit_gate','pay_gate')),
  outcome TEXT NOT NULL CHECK (outcome IN ('pass','soft_fail','hard_fail')),
  required_amount NUMERIC(14,4) NOT NULL,
  available_amount NUMERIC(14,4) NOT NULL,
  reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  idempotency_key TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS funding_gate_eval_by_run
  ON funding_gate_evaluation(tenant_id, pay_run_id, gate_type, evaluated_at);

COMMIT;
