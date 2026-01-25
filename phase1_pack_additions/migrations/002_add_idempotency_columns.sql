-- 002_add_idempotency_columns.sql
ALTER TABLE pay_statement
  ADD COLUMN calculation_id UUID NOT NULL;

ALTER TABLE pay_line_item
  ADD COLUMN calculation_id UUID NOT NULL,
  ADD COLUMN line_hash TEXT NOT NULL;

CREATE UNIQUE INDEX pli_line_hash_unique
  ON pay_line_item(pay_statement_id, calculation_id, line_hash);
